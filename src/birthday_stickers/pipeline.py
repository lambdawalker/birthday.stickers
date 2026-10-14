"""Sequential scene generation with durable, inspectable per-sticker artifacts."""

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .comfy import ComfyClient
from .config import Config, auth_headers
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


def write_prompts(path: Path, name: str, raw: str, refined: str | None = None) -> None:
    # Text fences keep literal reference tags visible in Markdown renderers.
    longest = max((len(match[0]) for match in re.finditer(r"`+", raw + (refined or ""))), default=0)
    fence = "`" * max(3, longest + 1)
    text = f"# {name}\n\n## Raw scene\n\n{fence}text\n{raw}\n{fence}\n"
    if refined is not None:
        text += f"\n## Refined scene\n\n{fence}text\n{refined}\n{fence}\n"
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def planning_prompt(
    template: str, iteration: int, count: int, name: str, previous_scenes: list[str]
) -> str:
    values = {
        "iteration": str(iteration),
        "count": str(count),
        "sticker_id": name,
        "previous_scenes": "\n".join(previous_scenes[-10:]) or "None yet.",
    }
    return re.sub(
        r"\{\{(iteration|count|sticker_id|previous_scenes)\}\}",
        lambda match: values[match[1]],
        template,
    )


def run(config: Config) -> list[Path]:
    local = validate_local(config)
    # Resolve required credentials before creating an output slot or making any calls.
    auth_headers(config.llm.api_key_env)
    comfy = ComfyClient(config.comfy)
    comfy.preflight(*local["graphs"])
    uploaded_references: dict[Path, str] = {}
    final_paths = []
    previous_scenes = []
    for iteration in range(config.generation.count):
        protagonist = secrets.choice(local["reference_pools"][0])
        creature = secrets.choice(local["reference_pools"][1])
        llm = LLMClient(config.llm, (protagonist, creature))
        index, name, metadata_path = reserve_sticker(config.output.directory)
        root = config.output.directory
        prompt_path = root / "prompts" / f"{name}.md"
        intermediate_path = root / "intermediate" / f"{name}.jpg"
        final_path = root / "final" / f"{name}.jpg"
        seed = (
            secrets.randbelow(SEED_MODULUS)
            if config.generation.seed is None
            else (config.generation.seed + 2 * index) % SEED_MODULUS
        )
        metadata = {
            "sticker": name,
            "index": index,
            "status": "planning",
            "created_at": datetime.now(UTC).isoformat(),
            "llm_model": config.llm.model,
            "qwen_seed": seed,
            "flux_seed": (seed + 1) % SEED_MODULUS,
            "generation_size": [config.generation.width, config.generation.height],
            "target_size": [config.output.width, config.output.height],
            "references": [
                {
                    "role": role,
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for role, path in (
                    ("protagonist", protagonist),
                    ("creature", creature),
                )
            ],
        }
        try:
            write_json(metadata_path, metadata)
            log.info("[%s/%s] %s: planning scene", iteration + 1, config.generation.count, name)
            raw = llm.complete(
                planning_prompt(
                    local["planning"], iteration + 1, config.generation.count, name, previous_scenes
                )
            )
            write_prompts(prompt_path, name, raw)
            metadata["status"] = "refining"
            write_json(metadata_path, metadata)
            log.info("%s: refining scene with local Qwen guide", name)
            refined = llm.complete(
                compose_refinement(local["refinement"], local["guide"], raw), refined=True
            )
            write_prompts(prompt_path, name, raw, refined)
            if "<image1>" not in refined or "<image2>" not in refined:
                raise ValueError(
                    "Refined prompt must name both <image1> and <image2> reference roles"
                )
            metadata["status"] = "rendering_qwen"
            write_json(metadata_path, metadata)
            for role, path in (("protagonist", protagonist), ("creature", creature)):
                if path not in uploaded_references:
                    uploaded_references[path] = comfy.upload(path, role)
            references = (uploaded_references[protagonist], uploaded_references[creature])
            graph = build_qwen(
                local["qwen"],
                config.qwen.nodes,
                config.qwen.overrides,
                refined,
                *references,
                config.generation.width,
                config.generation.height,
                seed,
                name + "-qwen",
            )
            write_json(root / "metadata" / f"{name}.qwen.json", graph)
            log.info("%s: rendering Qwen image", name)
            metadata["qwen_prompt_id"] = comfy.submit(graph)
            write_json(metadata_path, metadata)
            image_data = comfy.wait_image(metadata["qwen_prompt_id"], config.qwen.nodes["output"])
            save_jpeg(
                image_data,
                intermediate_path,
                config.output.jpeg_quality,
                config.output.jpeg_subsampling,
                config.output.jpeg_background,
            )
            source = decode_image(intermediate_path)
            padding = plan_padding(source.size, (config.output.width, config.output.height))
            metadata.update(status="intermediate_saved", padding=asdict(padding))
            write_json(metadata_path, metadata)
            if source.size == padding.target:
                shutil.copyfile(intermediate_path, final_path)
                metadata["outpaint_skipped"] = "Source already matches target size"
            else:
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
                log.info("%s: outpainting %sx%s to %sx%s", name, *source.size, *padding.target)
                metadata["status"] = "rendering_flux"
                metadata["flux_prompt_id"] = comfy.submit(graph)
                write_json(metadata_path, metadata)
                data = comfy.wait_image(metadata["flux_prompt_id"], config.flux.nodes["output"])
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
            log.info("%s: saved %s", name, final_path)
        except (Exception, KeyboardInterrupt) as error:
            failed_stage = metadata.get("status")
            metadata.update(
                status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                failed_stage=failed_stage,
                error=str(error),
            )
            try:
                write_json(metadata_path, metadata)
            except OSError:
                log.exception("Could not update metadata %s", metadata_path)
            if isinstance(error, KeyboardInterrupt):
                raise
            raise RuntimeError(
                f"{name} failed: {error}\nSaved progress: {metadata_path}"
            ) from error
    return final_paths
