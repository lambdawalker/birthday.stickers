"""Three workers with durable prompt/image handoff and optional GPU overlap."""

import hashlib
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock, Thread

from .activities import select_activity
from .artifacts import ensure_layout, result_path, stage_log
from .comfy import ComfyClient
from .config import Config, auth_headers
from .control import RunControl, RunHalted
from .http import RejectedSubmission
from .images import decode_image, finish_outpaint, plan_padding, save_jpeg
from .llm import LLMClient, compose_refinement
from .pipeline import (
    SEED_MODULUS,
    planning_prompt,
    prepare_records,
    reserve_sticker,
    validate_local,
    write_json,
    write_prompts,
)
from .references import reference_pools
from .retry import copyfile, read_bytes, read_text, retry_scope
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
            stage_log(
                self.config.output.directory, lane, name, stage=stage, completed=self.counts[lane]
            )
            self.progress(self.counts[lane], name, stage)
            self.worker_progress(lane, self.counts[lane], name, stage)

    def prepare_references(self, metadata, index):
        pools = reference_pools(self.config)
        selected = [
            (role, secrets.choice(pool))
            for role, pool in zip(
                self.config.reference_roles if self.config.preset else ("protagonist", "creature"),
                pools,
                strict=True,
            )
            if pool
        ]
        seed = (
            secrets.randbelow(SEED_MODULUS)
            if self.config.generation.seed is None
            else (self.config.generation.seed + 2 * index) % SEED_MODULUS
        )
        metadata.update(
            artifact_layout=2,
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
                    "sha256": hashlib.sha256(read_bytes(path)).hexdigest(),
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
        self.prompt_path = result_path(self.root, "prompts", self.name)
        self.intermediate = result_path(self.root, "images", self.name)
        self.final = result_path(
            self.root, "outpainting" if self.config.generation.outpainting else "images", self.name
        )

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
            stage_log(
                self.root, self.lane, self.name, error=str(error), outcome=self.meta["status"]
            )
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
            stage_log(self.root, self.lane, self.name, step=key, seconds=seconds, outcome=outcome)
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
            if hashlib.sha256(read_bytes(path)).hexdigest() != ref["sha256"]:
                raise ValueError(f"Selected reference changed since this scene started: {path}")
        return paths

    def prepare(self, iteration, previous_scenes):
        local = self.pipeline.local
        if "references" not in self.meta:
            self.pipeline.prepare_references(self.meta, int(self.name.split("-")[-1]))
            self.save()
        # Reference selection and preset rendering are separate checkpoints. A pause
        # or error while rendering may have saved references without a planning brief.
        if self.config.preset and "planning_prompt" not in self.meta:
            from .presets import select_scene

            selection = self.meta.get("scene_selection")
            if selection is None:
                selection = select_scene(
                    self.config.preset, self.root / "metadata", self.meta["references"]
                )
            self.meta.update(
                scene_selection=selection,
                activity=selection["activity"],
                planning_prompt=selection["prompt"],
            )
            self.save()
        references = self.references()
        llm = LLMClient(self.config.llm, references, self.config.generation.type)
        activity = self.meta.get("activity")
        if "raw_scene" not in self.meta:
            if self.config.preset and "planning_request" not in self.meta:
                self.meta["planning_request"] = (
                    self.meta["planning_prompt"]
                    + "\n\nRecent scenes (vary details):\n"
                    + "\n".join(previous_scenes[-10:])
                )
                self.save()
            with self.step("planning", "Plan scene"):
                self.meta["raw_scene"] = llm.complete(
                    self.meta["planning_request"]
                    if self.config.preset
                    else planning_prompt(
                        local["planning"],
                        iteration,
                        self.config.generation.count,
                        self.name,
                        previous_scenes,
                        activity,
                    )
                )
        raw = self.meta["raw_scene"]
        write_prompts(
            self.prompt_path,
            self.name,
            raw,
            self.meta.get("refined_scene"),
            activity,
            self.meta.get("planning_request"),
        )
        if "refined_scene" not in self.meta:
            with self.step("refining", "Refine Qwen prompt"):
                refined = llm.complete(
                    compose_refinement(local["refinement"], local["guide"], raw)
                    + (
                        "\n\nCharacter-source assignments:\n"
                        + json.dumps(self.meta["scene_selection"]["characters"], ensure_ascii=False)
                        if self.config.preset
                        else ""
                    ),
                    refined=True,
                )
                write_prompts(
                    self.prompt_path,
                    self.name,
                    raw,
                    refined,
                    activity,
                    self.meta.get("planning_request"),
                )
                required = [
                    f"<image{i}>" for i in range(1, len(self.config.flow.reference_roles) + 1)
                ]
                if any(tag not in refined for tag in required):
                    raise ValueError(
                        "Refined prompt must name reference roles: " + ", ".join(required)
                    )
                if self.config.preset and set(re.findall(r"<image\d+>", refined)) != set(required):
                    raise ValueError(
                        "Refinement image tags do not match assigned character references"
                    )
                if len(required) == 1 and "<image2>" in refined:
                    raise ValueError(
                        "This flow has no second reference; describe the dragon in text"
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
        if self.meta.get(pending) and not self.meta.get(f"{kind}_request_id"):
            raise ValueError(
                f"{kind} submission outcome is unknown. Inspect ComfyUI history and add {key} "
                f"to this scene's metadata, or remove {pending} only after confirming "
                "no job was queued."
            )
        write_json(self.root / "metadata" / f"{self.name}.{kind}.json", graph)
        self.pipeline.check()
        client = self.client(kind)
        recover = self.meta.get(pending, False)
        self.meta.setdefault(f"{kind}_request_id", uuid.uuid4().hex)
        self.meta[pending] = True
        self.save()
        try:
            self.meta[key] = client.submit(graph, self.meta[f"{kind}_request_id"], recover=recover)
        except BaseException as error:
            if isinstance(error, RejectedSubmission) or getattr(
                error, "submission_not_sent", False
            ):
                self.meta.pop(pending, None)
                self.meta.pop(f"{kind}_request_id", None)
                self.save()
            raise
        self.meta.pop(pending)
        self.save()

    def complete(self):
        self.meta["outpainting_saved" if self.lane == "outpainting" else "image_saved"] = True
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
            if not self.config.generation.outpainting:
                self.complete()
            return
        config, local, comfy = self.config, self.pipeline.local, self.client("qwen")
        references = self.references()
        refined = self.meta["refined_scene"]
        if not self.meta.get("intermediate_saved"):
            if references and "qwen_prompt_id" not in self.meta:
                with self.step("upload_references", "Upload character references"):
                    for role, path in zip(config.flow.reference_roles, references, strict=True):
                        self.pipeline.check()
                        key = (path, hashlib.sha256(read_bytes(path)).hexdigest())
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
                    self.meta["intermediate_saved"] = True
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
                self.meta["image_saved"] = True
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
                copyfile(self.intermediate, self.final)
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
                        negative=read_text(config.negative, encoding="utf-8").strip()
                        if config.negative
                        else None,
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
    ensure_layout(config.output.directory)
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
    if config.generation.outpainting and config.negative:
        cfg = pipeline.local["graphs"][1][str(config.flux.nodes["sampler"])]["inputs"].get("cfg", 1)
        if cfg == 1:
            message = (
                "Outpainting CFG is 1: negative conditioning is inactive; "
                "borderless instructions remain in the positive prompt."
            )
            log.info(message, extra={"worker": "outpainting"})
            stage_log(config.output.directory, "outpainting", "", message=message)
    mode, reason = gpu_policy(config)
    log.info("GPU mode: %s · %s", mode, reason)
    start = time.perf_counter()
    pipeline.comfy.preflight(pipeline.local["graphs"][0])
    if config.generation.outpainting:
        pipeline.comfy_outpainting.preflight(pipeline.local["graphs"][1])
    log.info("Service checks finished in %.2fs", time.perf_counter() - start)
    records = prepare_records(config) if resume else []
    for path, meta in records:
        while meta.get("retired_jobs"):
            job = meta["retired_jobs"][0]
            if job["server"] != config.outpainting_comfy.base_url.rstrip("/"):
                raise ValueError(
                    "Restore the original outpainting endpoint to settle its invalidated job"
                )
            client = ComfyClient(config.outpainting_comfy, "outpainting")
            pipeline.report("outpainting", meta["sticker"], "waiting_for_invalidated_job")
            client.wait_terminal(job["prompt_id"], pipeline.check)
            meta["retired_jobs"].pop(0)
            write_json(path, meta)
    # Disabling a stage cannot cancel work already submitted to its server.
    # Drain those jobs before starting more GPU work, including separate ports
    # that may share the same physical GPU. Keep IDs for later re-enabling.
    needs_generation = len(records) < config.generation.count or any(
        m.get("status") != "complete" and not m.get("intermediate_saved") for _, m in records
    )
    if (not config.generation.outpainting or mode == "sequential") and needs_generation:
        for path, meta in records:
            if meta.get("outpainting_saved") or meta.get("flux_drained"):
                continue
            if (
                meta.get("flux_submission_pending")
                and not meta.get("flux_prompt_id")
                and meta.get("flux_request_id")
            ):
                client = ComfyClient(config.outpainting_comfy, "outpainting")
                if meta.get(
                    "flux_server", client.config.base_url.rstrip("/")
                ) != client.config.base_url.rstrip("/"):
                    raise ValueError("Restore the original outpainting endpoint to recover its job")
                meta["flux_prompt_id"] = client.recover_submission(meta["flux_request_id"])
                meta.pop("flux_submission_pending", None)
                write_json(path, meta)
            if meta.get("flux_submission_pending") and not meta.get("flux_prompt_id"):
                raise ValueError(
                    "An earlier outpainting submission has an unknown outcome. "
                    "Resolve its saved job ID before starting more GPU work."
                )
            if not meta.get("flux_prompt_id"):
                continue
            if config.flux is None:
                raise ValueError("Keep the saved Flux workflow to finish its submitted job")
            pipeline.comfy_outpainting = ComfyClient(config.outpainting_comfy, "outpainting")
            scene = Scene(pipeline, path, meta, "outpainting")
            pipeline.report("outpainting", scene.name, "waiting_for_submitted_job")
            log.info(
                "Waiting for previously submitted outpainting before new GPU work",
                extra={"worker": "outpainting"},
            )
            scene.client("flux").wait_terminal(meta["flux_prompt_id"], pipeline.check)
            meta["flux_drained"] = True
            scene.save()
    completed = [(p, m) for p, m in records if m.get("status") == "complete"]
    final_paths = [
        result_path(
            config.output.directory,
            "outpainting" if config.generation.outpainting else "images",
            m["sticker"],
        )
        for _, m in completed
    ]
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
    images_succeeded = Event()
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
                    if mode == "concurrent":
                        pipeline.report("images", scene.name, "waiting_for_outpainting")
                        while not settled.wait(0.1):
                            pipeline.check()
                        pipeline.check()
                else:
                    final_paths.append(scene.final)
                if ready.empty() and not producer_done.is_set():
                    pipeline.report("images", "", "waiting_for_prompts")
            pipeline.report("images", "", "done")
            images_succeeded.set()
        finally:
            images_done.set()

    def extend():
        if not config.generation.outpainting:
            pipeline.report("outpainting", "", "disabled")
            return
        if mode == "sequential":
            pipeline.report("outpainting", "", "waiting_for_generation_batch")
            while not images_done.wait(0.1):
                pipeline.check()
            pipeline.check()
            if not images_succeeded.is_set():
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
            with retry_scope(
                pipeline.check,
                {produce: "prompts", consume: "images", extend: "outpainting"}[function],
            ):
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
