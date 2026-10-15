"""Shared scene artifacts and the public parallel pipeline entry point."""

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path

from .activities import load_activities
from .artifacts import STAGES, ensure_layout
from .config import Config
from .control import RunControl
from .images import decode_image, plan_padding
from .llm import compose_refinement
from .references import reference_pools
from .retry import file_retry, retry_scope
from .workflows import build_flux, load_workflow

log = logging.getLogger(__name__)
SEED_MODULUS = 2**63


@file_retry
def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@file_retry
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
        *([config.flux.path] if config.generation.outpainting and config.flux else []),
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
    graph = config.flow.build_graph(
        config, qwen, "validation", ("protagonist.png", "creature.png"), 0, "validation"
    )
    graphs = [graph]
    flux = None
    if config.generation.outpainting:
        if config.flux is None:
            raise ValueError("Outpainting requires workflows.flux")
        padding = plan_padding(
            (config.generation.width, config.generation.height), config.final_size
        )
        flux = load_workflow(config.flux.path)
        graphs.append(
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
                negative=config.negative.read_text(encoding="utf-8").strip()
                if config.negative
                else None,
            )
        )
    outpainting_prompt = config.output.outpainting_prompt
    if config.generation.outpainting and config.outpainting is not None:
        outpainting_prompt = config.outpainting.read_text(encoding="utf-8").strip()
        if not outpainting_prompt:
            raise ValueError(f"Outpainting prompt must not be empty: {config.outpainting}")
    return {
        "outpainting_prompt": outpainting_prompt,
        "activities": activities,
        "reference_pools": pools,
        "planning": planning,
        "guide": guide,
        "refinement": refinement,
        "qwen": qwen,
        "flux": flux,
        "graphs": graphs,
    }


@file_retry
def reserve_sticker(root: Path) -> tuple[int, str, Path]:
    ensure_layout(root)
    folders = [root / name for name in ("intermediate", "final", "prompts", "metadata")]
    folders += [root / stage / "results" for stage in STAGES.values()]
    folders = [folder for folder in folders if folder.is_dir()]
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


@file_retry
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
        "previous_scenes": "\n".join(previous_scenes[-3:]) or "None yet.",
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
    worker_progress: Callable[[str, int, str, str], None] = lambda *_: None,
) -> list[Path]:
    """Run prompt, image, and outpainting workers with durable scene handoff."""
    from .engine import execute

    control = control or RunControl()
    with retry_scope(control.check):
        return execute(config, resume, control, progress, worker_progress)


@file_retry
def prepare_records(config: Config) -> list[tuple[Path, dict]]:
    """Import legacy paths and reopen generated-only scenes when outpainting is enabled."""
    from .artifacts import generated_only, import_legacy, result_path
    from .regeneration import reconcile_outputs

    records = scene_records(config.output.directory)
    import_legacy(config.output.directory, records)
    for path, meta in records:
        original = dict(meta)
        was_generated_only = generated_only(meta)
        if meta.get("artifact_layout") != 2:
            meta["artifact_layout"] = 2
        if meta.get("status") == "complete" and not was_generated_only:
            meta["outpainting_saved"] = True
        reconcile_outputs(config, path, meta, write_json)
        image = result_path(config.output.directory, "images", meta["sticker"])
        if config.generation.outpainting != meta.get("outpainting", not was_generated_only):
            if image.is_file():
                decode_image(image)
                meta["intermediate_saved"] = True
                meta["image_saved"] = True
                meta["status"] = (
                    "complete"
                    if not config.generation.outpainting or meta.get("outpainting_saved")
                    else "image_ready"
                )
            elif meta.get("status") == "complete":
                raise ValueError(f"Cannot change outpainting: generated image is missing: {image}")
            meta["outpainting"] = config.generation.outpainting
            meta["target_size"] = list(config.final_size)
            meta.pop("outpaint_skipped", None)
        if meta != original:
            write_json(path, meta)
    return records
