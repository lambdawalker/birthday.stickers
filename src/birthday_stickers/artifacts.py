"""Stage-owned outputs and append-only execution logs."""

import json
from datetime import UTC, datetime
from pathlib import Path

from .retry import file_retry

STAGES = {"prompts": "scene_planning", "images": "image_generation", "outpainting": "outpainting"}


def result_path(root: Path, lane: str, name: str) -> Path:
    suffix = ".md" if lane == "prompts" else ".jpg"
    return root / STAGES[lane] / "results" / (name + suffix)


@file_retry
def ensure_layout(root: Path) -> None:
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    for stage in STAGES.values():
        for folder in ("results", "logs"):
            (root / stage / folder).mkdir(parents=True, exist_ok=True)


@file_retry
def stage_log(root: Path, lane: str, name: str, **event) -> None:
    path = root / STAGES[lane] / "logs" / f"{name or 'worker'}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": datetime.now(UTC).isoformat(), **event}) + "\n")
