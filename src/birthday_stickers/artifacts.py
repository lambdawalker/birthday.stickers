"""Stage-owned outputs and append-only execution logs."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

STAGES = {"prompts": "scene_planning", "images": "image_generation", "outpainting": "outpainting"}


def result_path(root: Path, lane: str, name: str) -> Path:
    suffix = ".md" if lane == "prompts" else ".jpg"
    return root / STAGES[lane] / "results" / (name + suffix)


def ensure_layout(root: Path) -> None:
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    for stage in STAGES.values():
        for folder in ("results", "logs"):
            (root / stage / folder).mkdir(parents=True, exist_ok=True)


def stage_log(root: Path, lane: str, name: str, **event) -> None:
    path = root / STAGES[lane] / "logs" / f"{name or 'worker'}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": datetime.now(UTC).isoformat(), **event}) + "\n")


def generated_only(meta: dict) -> bool:
    if "outpainting" in meta:
        return meta["outpainting"] is False
    if meta.get("outpaint_skipped") == "Disabled for this session":
        return True
    # The original solo flow had no outpainting setting or metadata marker.
    return meta.get("generation_type") in ("baby_dragon", "single_character_text")


def import_legacy(root: Path, records: list) -> None:
    """Copy old artifacts without deleting originals or overwriting new stage outputs."""
    ensure_layout(root)
    for _, meta in records:
        if meta.get("artifact_layout") == 2:
            continue
        name = meta["sticker"]
        sources = [
            ("prompts", root / "prompts" / f"{name}.md"),
            ("images", root / "intermediate" / f"{name}.jpg"),
            ("images" if generated_only(meta) else "outpainting", root / "final" / f"{name}.jpg"),
        ]
        for lane, source in sources:
            target = result_path(root, lane, name)
            if source.is_file() and not target.exists():
                temporary = target.with_suffix(target.suffix + ".tmp")
                shutil.copyfile(source, temporary)
                temporary.replace(target)
