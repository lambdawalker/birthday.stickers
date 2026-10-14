"""Three workers with durable prompt/image handoff and optional GPU overlap."""

import hashlib
import logging
import secrets
import shutil
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock, Thread

from .activities import select_activity
from .comfy import ComfyClient
from .config import Config, auth_headers
from .control import RunControl, RunHalted
from .images import decode_image, finish_outpaint, plan_padding, save_jpeg
from .llm import LLMClient, compose_refinement
from .pipeline import (
    SEED_MODULUS,
    planning_prompt,
    reserve_sticker,
    scene_records,
    validate_local,
    write_json,
    write_prompts,
)
from .references import reference_pools
from .scheduling import gpu_policy
from .workflows import build_flux

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config, control, progress, worker_progress):
        self.config = config
        self.control = control
        self.progress = progress
        self.worker_progress = worker_progress
        self.abort = Event()
        self.report_lock = RLock()
        self.counts = {"prompts": 0, "images": 0, "outpainting": 0}
        self.uploaded: dict[tuple[Path, str], str] = {}

    def check(self):
        self.control.check()
        if self.abort.is_set():
            raise RunHalted("interrupted")

    def report(self, lane, name, stage, increment=0):
        with self.report_lock:
            self.counts[lane] += increment
            self.progress(self.counts[lane], name, stage)
            self.worker_progress(lane, self.counts[lane], name, stage)

    def prepare_references(self, metadata, index):
        pools = reference_pools(self.config)
        selected = [
            (role, secrets.choice(pool))
            for role, pool in zip(("protagonist", "creature"), pools, strict=True)
            if pool
        ]
        seed = (
            secrets.randbelow(SEED_MODULUS)
            if self.config.generation.seed is None
            else (self.config.generation.seed + 2 * index) % SEED_MODULUS
        )
        metadata.update(
            created_at=datetime.now(UTC).isoformat(),
            llm_model=self.config.llm.model,
            generation_type=self.config.generation.type,
            qwen_seed=seed,
            flux_seed=(seed + 1) % SEED_MODULUS,
            generation_size=[self.config.generation.width, self.config.generation.height],
            target_size=list(self.config.final_size),
            outpainting=self.config.generation.outpainting,
            references=[
                {
                    "role": role,
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for role, path in selected
            ],
        )
        if self.local["activities"]:
            metadata["activity"] = select_activity(
                self.local["activities"], self.config.output.directory / "metadata"
            )


class Scene:
    def __init__(self, pipeline: Pipeline, path: Path, metadata: dict, lane: str):
        self.pipeline = pipeline
        self.config = pipeline.config
        self.path = path
        self.meta = metadata
        self.name = metadata["sticker"]
        self.lane = lane
        self.root = self.config.output.directory
        self.prompt_path = self.root / "prompts" / f"{self.name}.md"
        self.intermediate = self.root / "intermediate" / f"{self.name}.jpg"
        self.final = self.root / "final" / f"{self.name}.jpg"

    def save(self):
        write_json(self.path, self.meta)

    @contextmanager
    def guard(self):
        try:
            yield
        except (Exception, KeyboardInterrupt) as error:
            self.meta.update(
                failed_stage=self.meta.get("status"),
                status=(
                    error.status
                    if isinstance(error, RunHalted)
                    else "interrupted"
                    if isinstance(error, KeyboardInterrupt)
                    else "failed"
                ),
                error=str(error),
            )
            self.save()
            if isinstance(error, (RunHalted, KeyboardInterrupt)):
                raise
            raise RuntimeError(
                f"{self.name} failed: {error}\nSaved progress: {self.path}"
            ) from error

    @contextmanager
    def step(self, key, label):
        self.pipeline.check()
        self.meta["status"] = key
        self.meta.pop("error", None)
        self.save()
        self.pipeline.report(self.lane, self.name, key)
        self.pipeline.check()
        started = time.perf_counter()
        outcome = "complete"
        log.info("[%s] %s | %s started", self.lane, self.name, label, extra={"worker": self.lane})
        try:
            yield
        except BaseException as error:
            outcome = error.status if isinstance(error, RunHalted) else "failed"
            raise
        finally:
            seconds = time.perf_counter() - started
            self.meta.setdefault("timings", []).append(
                {
                    "step": key,
                    "seconds": round(seconds, 6),
                    "outcome": outcome,
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
            )
            self.save()
            log.info(
                "[%s] %s | %s %s in %.2fs",
                self.lane,
                self.name,
                label,
                "finished" if outcome == "complete" else outcome,
                seconds,
                extra={"worker": self.lane},
            )

    def references(self):
        paths = tuple(Path(ref["path"]) for ref in self.meta["references"])
        for path, ref in zip(paths, self.meta["references"], strict=True):
            if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
                raise ValueError(f"Selected reference changed since this scene started: {path}")
        return paths

    def prepare(self, iteration, previous_scenes):
        local = self.pipeline.local
        if "references" not in self.meta:
            self.pipeline.prepare_references(self.meta, int(self.name.split("-")[-1]))
            self.save()
        references = self.references()
        llm = LLMClient(self.config.llm, references, self.config.generation.type)
        activity = self.meta.get("activity")
        if "raw_scene" not in self.meta:
            with self.step("planning", "Plan scene"):
                self.meta["raw_scene"] = llm.complete(
                    planning_prompt(
                        local["planning"],
                        iteration,
                        self.config.generation.count,
                        self.name,
                        previous_scenes,
                        activity,
                    )
                )
        raw = self.meta["raw_scene"]
        write_prompts(self.prompt_path, self.name, raw, self.meta.get("refined_scene"), activity)
        if "refined_scene" not in self.meta:
            with self.step("refining", "Refine Qwen prompt"):
                refined = llm.complete(
                    compose_refinement(local["refinement"], local["guide"], raw), refined=True
                )
                write_prompts(self.prompt_path, self.name, raw, refined, activity)
                if self.config.flow.uses_references and (
                    "<image1>" not in refined or "<image2>" not in refined
                ):
                    raise ValueError(
                        "Refined prompt must name both <image1> and <image2> reference roles"
                    )
                self.meta["refined_scene"] = refined
        self.meta["status"] = "prompts_ready"
        self.save()

    def client(self, kind):
        client = (
            getattr(self.pipeline, "comfy_outpainting", self.pipeline.comfy)
            if kind == "flux"
            else self.pipeline.comfy
        )
        endpoint = client.config.base_url.rstrip("/")
        key = f"{kind}_server"
        if self.meta.get(f"{kind}_prompt_id") or self.meta.get(f"{kind}_submission_pending"):
            previous = self.meta.get(key)
            if previous is not None and previous != endpoint:
                raise ValueError(
                    f"{kind} job belongs to {previous}; restore that endpoint to resume"
                )
        self.meta[key] = endpoint
        return client

    def submit(self, kind, graph):
        key = f"{kind}_prompt_id"
        pending = f"{kind}_submission_pending"
        if self.meta.get(pending):
            raise ValueError(
                f"{kind} submission outcome is unknown. Inspect ComfyUI history and add {key} "
                f"to this scene's metadata, or remove {pending} only after confirming "
                "no job was queued."
            )
        write_json(self.root / "metadata" / f"{self.name}.{kind}.json", graph)
        self.pipeline.check()
        self.meta[pending] = True
        self.save()
        self.meta[key] = self.client(kind).submit(graph)
        self.meta.pop(pending)
        self.save()

    def complete(self):
        self.meta.update(status="complete", finished_at=datetime.now(UTC).isoformat())
        self.save()
        self.pipeline.report(self.lane, self.name, "complete", increment=1)

    def render(self):
        """Compatibility entry point for running one scene synchronously."""
        self.generate_image()
        if self.config.generation.outpainting:
            self.lane = "outpainting"
            self.outpaint()

    def generate_image(self):
        if self.meta.get("intermediate_saved"):
            return
        config, local, comfy = self.config, self.pipeline.local, self.client("qwen")
        references = self.references()
        refined = self.meta["refined_scene"]
        if not self.meta.get("intermediate_saved"):
            if references and "qwen_prompt_id" not in self.meta:
                with self.step("upload_references", "Upload character references"):
                    for role, path in zip(("protagonist", "creature"), references, strict=True):
                        self.pipeline.check()
                        key = (path, hashlib.sha256(path.read_bytes()).hexdigest())
                        if key not in self.pipeline.uploaded:
                            self.pipeline.uploaded[key] = comfy.upload(path, role)
            with self.step("rendering_qwen", "Qwen render and download"):
                if "qwen_prompt_id" not in self.meta:
                    uploaded_refs = tuple(
                        self.pipeline.uploaded[(p, ref["sha256"])]
                        for p, ref in zip(references, self.meta["references"], strict=True)
                    )
                    graph = config.flow.build_graph(
                        config,
                        local["qwen"],
                        refined,
                        uploaded_refs,
                        self.meta["qwen_seed"],
                        self.name + "-qwen",
                    )
                    self.submit("qwen", graph)
                data = comfy.wait_image(
                    self.meta["qwen_prompt_id"], config.qwen.nodes["output"], self.pipeline.check
                )
            if not config.generation.outpainting:
                with self.step("save_final", "Save final JPEG (outpainting off)"):
                    image = decode_image(data)
                    if image.size != config.final_size:
                        raise ValueError("Qwen returned an unexpected image size")
                    save_jpeg(
                        image,
                        self.final,
                        config.output.jpeg_quality,
                        config.output.jpeg_subsampling,
                        config.output.jpeg_background,
                    )
                    self.meta["outpaint_skipped"] = "Disabled for this session"
                self.complete()
                return
            with self.step("save_intermediate", "Save intermediate JPEG"):
                save_jpeg(
                    data,
                    self.intermediate,
                    config.output.jpeg_quality,
                    config.output.jpeg_subsampling,
                    config.output.jpeg_background,
                )
                self.meta["intermediate_saved"] = True
        self.meta["status"] = "image_ready"
        self.save()
        self.pipeline.report("images", self.name, "image_ready", increment=1)

    def outpaint(self):
        config, local, comfy = self.config, self.pipeline.local, self.client("flux")
        raw = self.meta["raw_scene"]
        source = decode_image(self.intermediate)
        padding = plan_padding(source.size, config.final_size)
        self.meta["padding"] = asdict(padding)
        if source.size == padding.target:
            with self.step("save_final", "Save final JPEG (outpainting not needed)"):
                shutil.copyfile(self.intermediate, self.final)
                self.meta["outpaint_skipped"] = "Source already matches target size"
        else:
            if "flux_prompt_id" not in self.meta:
                with self.step("upload_intermediate", "Upload intermediate image"):
                    uploaded = comfy.upload(self.intermediate, "intermediate")
                    self.meta["outpainting_prompt"] = "\n\n".join(
                        text for text in (raw, local["outpainting_prompt"].strip()) if text
                    )
                    graph = build_flux(
                        local["flux"],
                        config.flux.nodes,
                        config.flux.overrides,
                        self.meta["outpainting_prompt"],
                        uploaded,
                        padding,
                        config.output.feathering,
                        self.meta["flux_seed"],
                        self.name + "-flux",
                    )
            with self.step("rendering_flux", "Flux outpaint and download"):
                if "flux_prompt_id" not in self.meta:
                    self.submit("flux", graph)
                data = comfy.wait_image(
                    self.meta["flux_prompt_id"], config.flux.nodes["output"], self.pipeline.check
                )
            with self.step("save_final", "Composite and save final JPEG"):
                final = finish_outpaint(
                    decode_image(data),
                    source,
                    padding,
                    config.output.feathering,
                    config.output.preserve_center,
                )
                save_jpeg(
                    final,
                    self.final,
                    config.output.jpeg_quality,
                    config.output.jpeg_subsampling,
                    config.output.jpeg_background,
                )
        self.complete()


def execute(
    config: Config,
    resume: bool,
    control: RunControl,
    progress: Callable,
    worker_progress: Callable,
) -> list[Path]:
    pipeline = Pipeline(config, control, progress, worker_progress)
    pipeline.check()
    pipeline.local = validate_local(config)
    auth_headers(config.llm.api_key_env)
    pipeline.comfy = ComfyClient(config.comfy, "images")
    pipeline.comfy_outpainting = (
        ComfyClient(config.outpainting_comfy, "outpainting")
        if config.generation.outpainting
        else pipeline.comfy
    )
    mode, reason = gpu_policy(config)
    log.info("GPU mode: %s · %s", mode, reason)
    start = time.perf_counter()
    pipeline.comfy.preflight(pipeline.local["graphs"][0])
    if config.generation.outpainting:
        pipeline.comfy_outpainting.preflight(pipeline.local["graphs"][1])
    log.info("Service checks finished in %.2fs", time.perf_counter() - start)
    records = scene_records(config.output.directory) if resume else []
    completed = [(p, m) for p, m in records if m.get("status") == "complete"]
    final_paths = [config.output.directory / "final" / f"{m['sticker']}.jpg" for _, m in completed]
    for path in final_paths:
        if decode_image(path).size != config.final_size:
            raise ValueError(f"Completed image is missing or has the wrong dimensions: {path}")
    pending = [(p, m) for p, m in records if m.get("status") != "complete"]
    pipeline.counts["images"] = sum(
        bool(m.get("intermediate_saved")) or m.get("status") == "complete" for _, m in records
    )
    pipeline.counts["outpainting"] = len(completed) if config.generation.outpainting else 0
    pipeline.counts["prompts"] = sum(
        "refined_scene" in m or m.get("status") == "complete" for _, m in records
    )
    pipeline.report("images", "", "gpu_mode_" + mode)
    ready: Queue[tuple[Path, dict]] = Queue()
    images: Queue[tuple[Path, dict, Event]] = Queue()
    images_done = Event()
    producer_done = Event()
    errors: Queue[BaseException] = Queue()

    def produce():
        previous = [m["raw_scene"] for _, m in completed if "raw_scene" in m]
        try:
            for iteration in range(len(completed), config.generation.count):
                pipeline.check()
                if pending:
                    path, meta = pending.pop(0)
                else:
                    index, name, path = reserve_sticker(config.output.directory)
                    meta = {"sticker": name, "index": index, "status": "reserved"}
                scene = Scene(pipeline, path, meta, "prompts")
                was_ready = "refined_scene" in meta
                with scene.guard():
                    if not was_ready:
                        scene.prepare(iteration + 1, previous)
                    previous.append(meta["raw_scene"])
                    pipeline.report("prompts", scene.name, "prompts_ready", increment=not was_ready)
                # Ownership transfers here. The producer never modifies this scene again.
                ready.put((path, meta))
            pipeline.report("prompts", "", "done")
        finally:
            producer_done.set()

    def consume():
        pipeline.report("images", "", "waiting_for_prompts")
        try:
            while True:
                pipeline.check()
                try:
                    path, meta = ready.get(timeout=0.1)
                except Empty:
                    if producer_done.is_set() and ready.empty():
                        break
                    continue
                scene = Scene(pipeline, path, meta, "images")
                with scene.guard():
                    scene.generate_image()
                if config.generation.outpainting:
                    settled = Event()
                    # Transfer ownership; this worker must not write scene metadata again.
                    images.put((path, meta, settled))
                    if mode == "sequential":
                        pipeline.report("images", scene.name, "waiting_for_outpainting")
                        while not settled.wait(0.1):
                            pipeline.check()
                        pipeline.check()
                else:
                    final_paths.append(scene.final)
                if ready.empty() and not producer_done.is_set():
                    pipeline.report("images", "", "waiting_for_prompts")
            pipeline.report("images", "", "done")
        finally:
            images_done.set()

    def extend():
        if not config.generation.outpainting:
            pipeline.report("outpainting", "", "disabled")
            return
        pipeline.report("outpainting", "", "waiting_for_images")
        while True:
            pipeline.check()
            try:
                path, meta, settled = images.get(timeout=0.1)
            except Empty:
                if images_done.is_set() and images.empty():
                    break
                continue
            scene = Scene(pipeline, path, meta, "outpainting")
            try:
                with scene.guard():
                    scene.outpaint()
                    final_paths.append(scene.final)
            finally:
                settled.set()
            if images.empty() and not images_done.is_set():
                pipeline.report("outpainting", "", "waiting_for_images")
        pipeline.report("outpainting", "", "done")

    finished = [Event(), Event(), Event()]
    launch = Event()

    def worker(function, done):
        try:
            launch.wait()
            pipeline.check()
            function()
        except BaseException as error:
            errors.put(error)
            pipeline.abort.set()
        finally:
            done.set()

    threads = [
        Thread(target=worker, args=(produce, finished[0]), name="sticker-prompts"),
        Thread(target=worker, args=(consume, finished[1]), name="sticker-images"),
        Thread(target=worker, args=(extend, finished[2]), name="sticker-outpainting"),
    ]
    interrupted = False
    startup_error = None
    try:
        for thread in threads:
            thread.start()
        launch.set()
    except BaseException as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        startup_error = None if interrupted else error
        control.request("stopped")
        launch.set()
    # A signal during Thread.join can corrupt its is_alive state on CPython.
    # Keep every completion check inside the handler, including startup bookkeeping.
    while True:
        try:
            pending_finishes = [
                done
                for thread, done in zip(threads, finished, strict=True)
                if thread.ident is not None
            ]
            if all(done.is_set() for done in pending_finishes):
                break
            for done in pending_finishes:
                done.wait(timeout=0.1)
        except KeyboardInterrupt:
            interrupted = True
            control.request("stopped")
    if startup_error is not None:
        raise startup_error
    if interrupted:
        raise KeyboardInterrupt()
    failures = []
    while not errors.empty():
        failures.append(errors.get_nowait())
    if failures:
        # Preserve the actual failure, not the peer's cooperative shutdown exception.
        raise next((e for e in failures if not isinstance(e, RunHalted)), failures[0])
    if len(final_paths) != config.generation.count:
        raise RuntimeError("The image worker finished before all session images were saved")
    return sorted(final_paths)
