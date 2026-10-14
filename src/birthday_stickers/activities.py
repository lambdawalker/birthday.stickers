"""Activity sampling without replacement, reconstructed from durable scene metadata."""

import json
import random
import re
from pathlib import Path

RNG = random.SystemRandom()


def load_activities(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read activity catalog {path}: {error}") from error
    ids = set()
    if not isinstance(rows, list) or not rows:
        raise ValueError("The activity catalog must be a nonempty JSON list")
    for row in rows:
        if (
            not isinstance(row, dict)
            or any(
                not isinstance(row.get(k), str) or not row[k].strip()
                for k in ("id", "category", "activity")
            )
            or not isinstance(row.get("settings"), list)
            or not row["settings"]
            or any(not isinstance(s, str) or not s.strip() for s in row["settings"])
        ):
            raise ValueError("Each activity needs id, category, activity, and nonempty settings")
        if row["id"] in ids:
            raise ValueError(f"Duplicate activity ID: {row['id']}")
        ids.add(row["id"])
    return rows


def select_activity(catalog: list[dict], metadata_directory: Path) -> dict:
    """Use one process per output directory; failed attempts count as selections."""
    ids = {row["id"] for row in catalog}
    used = set()
    cycle = 1
    previous_category = None
    paths = []
    for path in metadata_directory.glob("stiker-*.json"):
        match = re.fullmatch(r"stiker-(\d+)\.json", path.name)
        if match:
            paths.append((int(match[1]), path))
    for _, path in sorted(paths):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError("expected an object")
            selected = meta.get("activity")
            if selected is None:
                continue
            if not isinstance(selected, dict) or not isinstance(selected.get("id"), str):
                raise ValueError("invalid activity record")
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read activity history {path}: {error}") from error
        if selected["id"] in ids:
            used.add(selected["id"])
            previous_category = selected.get("category")
            if used == ids:
                used.clear()
                cycle += 1
    remaining = [row for row in catalog if row["id"] not in used]
    varied = [row for row in remaining if row["category"] != previous_category]
    row = RNG.choice(varied or remaining)
    return {
        "id": row["id"],
        "category": row["category"],
        "activity": row["activity"],
        "setting": RNG.choice(row["settings"]),
        "cycle": cycle,
        "mood": RNG.choice(["playful", "curious", "cozy", "quietly triumphant", "affectionate"]),
        "composition": RNG.choice(
            [
                "full-body interaction at the child's eye level",
                "full-body action in a gentle three-quarter view",
                "a wider environmental scene with both characters clearly readable",
            ]
        ),
        "palette": RNG.choice(
            [
                "blush pink, lavender, and powder blue",
                "powder blue, pale butter yellow, and peach",
                "lavender, blush pink, and pale butter yellow",
                "peach, blush pink, and powder blue",
            ]
        ),
    }
