"""Shared scene artifacts and the public parallel pipeline entry point."""

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path

from .activities import load_activities
from .config import Config
from .control import RunControl
from .images import decode_image, plan_padding
from .llm import compose_refinement
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
    worker_progress: Callable[[str, int, str, str], None] = lambda *_: None,
) -> list[Path]:
    """Run an LLM producer and a ComfyUI consumer with durable scene handoff."""
    from .engine import execute

    return execute(config, resume, control or RunControl(), progress, worker_progress)
