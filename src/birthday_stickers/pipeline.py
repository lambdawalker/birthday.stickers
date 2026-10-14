"""Sequential scene generation with durable, inspectable per-sticker artifacts."""

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .activities import load_activities, select_activity
from .comfy import ComfyClient
from .config import Config, auth_headers
from .control import RunControl, RunHalted
from .images import decode_image, finish_outpaint, plan_padding, save_jpeg
from .llm import LLMClient, compose_refinement
from .references import reference_pools
from .workflows import build_flux, build_qwen, load_workflow

log = logging.getLogger(__name__)
SEED_MODULUS = 2**63


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_local(config: Config) -> dict:
    activities = load_activities(config.activities) if config.activities else None
    pools = reference_pools(config)
    for path in (
        *pools[0],
        *pools[1],
        config.planning,
        config.refinement,
        config.guide,
        config.qwen.path,
        config.flux.path,
    ):
        if not path.is_file():
            raise ValueError(f"Required file is missing: {path}")
    for path in (*pools[0], *pools[1]):
        decode_image(path)
    planning = config.planning.read_text(encoding="utf-8").strip()
    guide = config.guide.read_text(encoding="utf-8").strip()
    refinement = config.refinement.read_text(encoding="utf-8").strip()
    if not planning or not guide:
        raise ValueError("Planning prompt and Qwen guide must not be empty")
    compose_refinement(refinement, guide, "validation scene")
    qwen = load_workflow(config.qwen.path)
    flux = load_workflow(config.flux.path)
    padding = plan_padding(
        (config.generation.width, config.generation.height),
        (config.output.width, config.output.height),
    )
    graphs = (
        build_qwen(
            qwen,
            config.qwen.nodes,
            config.qwen.overrides,
            "validation",
            "protagonist.png",
            "creature.png",
            config.generation.width,
            config.generation.height,
            0,
            "validation",
        ),
        build_flux(
            flux,
            config.flux.nodes,
            config.flux.overrides,
            "validation",
            "intermediate.jpg",
            padding,
            config.output.feathering,
            0,
            "validation",
        ),
    )
    return {
        "activities": activities,
        "reference_pools": pools,
        "planning": planning,
        "guide": guide,
        "refinement": refinement,
        "qwen": qwen,
        "flux": flux,
        "graphs": graphs,
    }


def reserve_sticker(root: Path) -> tuple[int, str, Path]:
    folders = [root / name for name in ("intermediate", "final", "prompts", "metadata")]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(r"^stiker-(\d+)\.")
    used = [
        int(match[1])
        for folder in folders
        for path in folder.iterdir()
        if (match := pattern.match(path.name))
    ]
    index = max(used, default=-1) + 1
    while True:
        name = f"stiker-{index:04d}"
        path = root / "metadata" / f"{name}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump({"status": "reserved", "sticker": name}, handle)
            return index, name, path
        except FileExistsError:
            index += 1


def write_prompts(
    path: Path, name: str, raw: str, refined: str | None = None, activity: dict | None = None
) -> None:
    # Text fences keep literal reference tags visible in Markdown renderers.
    longest = max((len(match[0]) for match in re.finditer(r"`+", raw + (refined or ""))), default=0)
    fence = "`" * max(3, longest + 1)
    text = f"# {name}\n\n## Raw scene\n\n{fence}text\n{raw}\n{fence}\n"
    if activity:
        text += (
            "\n## Selected activity\n\n"
            + "\n".join(f"- {key}: {value}" for key, value in activity.items())
            + "\n"
        )
    if refined is not None:
        text += f"\n## Refined scene\n\n{fence}text\n{refined}\n{fence}\n"
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def planning_prompt(
    template: str,
    iteration: int,
    count: int,
    name: str,
    previous_scenes: list[str],
    activity: dict | None = None,
) -> str:
    values = {
        "iteration": str(iteration),
        "count": str(count),
        "sticker_id": name,
        "previous_scenes": "\n".join(previous_scenes[-10:]) or "None yet.",
    }
    rendered = re.sub(
        r"\{\{(iteration|count|sticker_id|previous_scenes)\}\}",
        lambda match: values[match[1]],
        template,
    )
    if activity:
        rendered += "\n\nSelected scene brief (follow this activity; adapt details coherently):\n"
        rendered += "\n".join(
            f"{key}: {value}" for key, value in activity.items() if key not in ("id", "cycle")
        )
    return rendered


def scene_records(root: Path) -> list[tuple[Path, dict]]:
    records = []
    for path in (root / "metadata").glob("stiker-*.json"):
        if re.fullmatch(r"stiker-\d+\.json", path.name):
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or "sticker" not in value:
                raise ValueError(f"Invalid scene metadata: {path}")
            records.append((path, value))
    return sorted(records, key=lambda pair: int(pair[0].stem.split("-")[-1]))


def run(
    config: Config,
    *,
    resume: bool = False,
    control: RunControl | None = None,
    progress: Callable[[int, str, str], None] = lambda *_: None,
) -> list[Path]:
    control = control or RunControl()
    control.check()
    local = validate_local(config)
    auth_headers(config.llm.api_key_env)
    comfy = ComfyClient(config.comfy)
    comfy.preflight(*local["graphs"])
    control.check()
    root = config.output.directory
    records = scene_records(root) if resume else []
    completed = [m for _, m in records if m.get("status") == "complete"]
    final_paths = [root / "final" / f"{m['sticker']}.jpg" for m in completed]
    for path in final_paths:
        if decode_image(path).size != (config.output.width, config.output.height):
            raise ValueError(f"Completed image is missing or has the wrong dimensions: {path}")
    pending = [(p, m) for p, m in records if m.get("status") != "complete"]
    previous_scenes = [m["raw_scene"] for m in completed if "raw_scene" in m]
    uploaded_references: dict[Path, str] = {}
    for iteration in range(len(completed), config.generation.count):
        control.check()
        if pending:
            metadata_path, metadata = pending.pop(0)
            name = metadata["sticker"]
            index = int(name.split("-")[-1])
        else:
            index, name, metadata_path = reserve_sticker(root)
            metadata = {"sticker": name, "index": index, "status": "reserved"}
        prompt_path = root / "prompts" / f"{name}.md"
        intermediate_path = root / "intermediate" / f"{name}.jpg"
        final_path = root / "final" / f"{name}.jpg"

        def stage(
            status: str,
            metadata=metadata,
            metadata_path=metadata_path,
            iteration=iteration,
            name=name,
        ) -> None:
            control.check()
            metadata["status"] = status
            metadata.pop("error", None)
            write_json(metadata_path, metadata)
            progress(iteration, name, status)
            control.check()
            log.info("[%s/%s] %s: %s", iteration + 1, config.generation.count, name, status)

        try:
            if "references" not in metadata:
                protagonist = secrets.choice(local["reference_pools"][0])
                creature = secrets.choice(local["reference_pools"][1])
                seed = (
                    secrets.randbelow(SEED_MODULUS)
                    if config.generation.seed is None
                    else (config.generation.seed + 2 * index) % SEED_MODULUS
                )
                metadata.update(
                    created_at=datetime.now(UTC).isoformat(),
                    llm_model=config.llm.model,
                    qwen_seed=seed,
                    flux_seed=(seed + 1) % SEED_MODULUS,
                    generation_size=[config.generation.width, config.generation.height],
                    target_size=[config.output.width, config.output.height],
                    references=[
                        {
                            "role": role,
                            "path": str(path),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                        for role, path in (("protagonist", protagonist), ("creature", creature))
                    ],
                )
                if local["activities"]:
                    metadata["activity"] = select_activity(local["activities"], root / "metadata")
                write_json(metadata_path, metadata)
            references = tuple(Path(ref["path"]) for ref in metadata["references"])
            for path, ref in zip(references, metadata["references"], strict=True):
                if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
                    raise ValueError(f"Selected reference changed since this scene started: {path}")
            llm = LLMClient(config.llm, references)
            activity = metadata.get("activity")
            if "raw_scene" not in metadata:
                stage("planning")
                metadata["raw_scene"] = llm.complete(
                    planning_prompt(
                        local["planning"],
                        iteration + 1,
                        config.generation.count,
                        name,
                        previous_scenes,
                        activity,
                    )
                )
                write_json(metadata_path, metadata)
            raw = metadata["raw_scene"]
            write_prompts(prompt_path, name, raw, metadata.get("refined_scene"), activity)
            if "refined_scene" not in metadata:
                stage("refining")
                refined = llm.complete(
                    compose_refinement(local["refinement"], local["guide"], raw), refined=True
                )
                write_prompts(prompt_path, name, raw, refined, activity)
                if "<image1>" not in refined or "<image2>" not in refined:
                    raise ValueError(
                        "Refined prompt must name both <image1> and <image2> reference roles"
                    )
                metadata["refined_scene"] = refined
                write_json(metadata_path, metadata)
            refined = metadata["refined_scene"]
            if not metadata.get("intermediate_saved"):
                stage("rendering_qwen")
                if "qwen_prompt_id" not in metadata:
                    if metadata.get("qwen_submission_pending"):
                        raise ValueError(
                            "Qwen submission outcome is unknown. Inspect ComfyUI history and add "
                            "qwen_prompt_id to this scene's metadata, or remove "
                            "qwen_submission_pending only after confirming no job was queued."
                        )
                    for role, path in zip(("protagonist", "creature"), references, strict=True):
                        control.check()
                        if path not in uploaded_references:
                            uploaded_references[path] = comfy.upload(path, role)
                    graph = build_qwen(
                        local["qwen"],
                        config.qwen.nodes,
                        config.qwen.overrides,
                        refined,
                        *(uploaded_references[p] for p in references),
                        config.generation.width,
                        config.generation.height,
                        metadata["qwen_seed"],
                        name + "-qwen",
                    )
                    write_json(root / "metadata" / f"{name}.qwen.json", graph)
                    control.check()
                    metadata["qwen_submission_pending"] = True
                    write_json(metadata_path, metadata)
                    metadata["qwen_prompt_id"] = comfy.submit(graph)
                    metadata.pop("qwen_submission_pending")
                    write_json(metadata_path, metadata)
                image_data = comfy.wait_image(
                    metadata["qwen_prompt_id"], config.qwen.nodes["output"], control.check
                )
                save_jpeg(
                    image_data,
                    intermediate_path,
                    config.output.jpeg_quality,
                    config.output.jpeg_subsampling,
                    config.output.jpeg_background,
                )
                metadata["intermediate_saved"] = True
                write_json(metadata_path, metadata)
            source = decode_image(intermediate_path)
            padding = plan_padding(source.size, (config.output.width, config.output.height))
            metadata.update(padding=asdict(padding))
            if source.size == padding.target:
                control.check()
                shutil.copyfile(intermediate_path, final_path)
                metadata["outpaint_skipped"] = "Source already matches target size"
            else:
                stage("rendering_flux")
                if "flux_prompt_id" not in metadata:
                    if metadata.get("flux_submission_pending"):
                        raise ValueError(
                            "Flux submission outcome is unknown. Inspect ComfyUI history and add "
                            "flux_prompt_id to this scene's metadata, or remove "
                            "flux_submission_pending only after confirming no job was queued."
                        )
                    uploaded = comfy.upload(intermediate_path, "intermediate")
                    graph = build_flux(
                        local["flux"],
                        config.flux.nodes,
                        config.flux.overrides,
                        raw,
                        uploaded,
                        padding,
                        config.output.feathering,
                        metadata["flux_seed"],
                        name + "-flux",
                    )
                    write_json(root / "metadata" / f"{name}.flux.json", graph)
                    control.check()
                    metadata["flux_submission_pending"] = True
                    write_json(metadata_path, metadata)
                    metadata["flux_prompt_id"] = comfy.submit(graph)
                    metadata.pop("flux_submission_pending")
                    write_json(metadata_path, metadata)
                data = comfy.wait_image(
                    metadata["flux_prompt_id"], config.flux.nodes["output"], control.check
                )
                final = finish_outpaint(
                    decode_image(data),
                    source,
                    padding,
                    config.output.feathering,
                    config.output.preserve_center,
                )
                save_jpeg(
                    final,
                    final_path,
                    config.output.jpeg_quality,
                    config.output.jpeg_subsampling,
                    config.output.jpeg_background,
                )
            metadata.update(status="complete", finished_at=datetime.now(UTC).isoformat())
            write_json(metadata_path, metadata)
            previous_scenes.append(raw)
            final_paths.append(final_path)
            progress(iteration + 1, name, "complete")
            log.info("%s: saved %s", name, final_path)
        except (Exception, KeyboardInterrupt) as error:
            metadata.update(
                failed_stage=metadata.get("status"),
                status=(
                    error.status
                    if isinstance(error, RunHalted)
                    else "interrupted"
                    if isinstance(error, KeyboardInterrupt)
                    else "failed"
                ),
                error=str(error),
            )
            write_json(metadata_path, metadata)
            if isinstance(error, (RunHalted, KeyboardInterrupt)):
                raise
            raise RuntimeError(
                f"{name} failed: {error}\nSaved progress: {metadata_path}"
            ) from error
    return final_paths
