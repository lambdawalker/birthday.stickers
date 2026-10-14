"""Discover reference variants by uppercase character ID and numeric suffix."""

import re
from pathlib import Path

from .config import Config

FILENAME = re.compile(r"([A-Z])[0-9]+\.(?i:jpg)")


def discover_characters(directory: Path) -> dict[str, list[Path]]:
    if not directory.is_dir():
        raise ValueError(f"Character reference directory is missing: {directory}")
    groups: dict[str, list[Path]] = {}
    for path in sorted(directory.iterdir()):
        match = FILENAME.fullmatch(path.name)
        if path.is_file() and match:
            groups.setdefault(match[1], []).append(path)
    return groups


def reference_pools(config: Config) -> tuple[list[Path], list[Path]]:
    if config.generation.type == "baby_dragon":
        return [], []
    if config.character_directory is None:
        # Keep existing configs with explicit image paths working.
        return [Path(config.protagonist)], [Path(config.creature)]
    groups = discover_characters(config.character_directory)
    pools = []
    for role, character_id in (("protagonist", config.protagonist), ("creature", config.creature)):
        candidates = groups.get(str(character_id), [])
        if not candidates:
            raise ValueError(
                f"No references for {role} character {character_id} in "
                f"{config.character_directory}; expected {character_id}000.jpg, "
                f"{character_id}001.jpg, ..."
            )
        pools.append(candidates)
    return pools[0], pools[1]
