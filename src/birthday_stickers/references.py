"""Discover live reference variants by configured prefix and numeric suffix."""

import re
from pathlib import Path

from .character_conversion import convert_characters
from .config import Config
from .retry import file_retry

FILENAME = re.compile(r"([A-Z])[0-9]+\.(?i:jpg)")


@file_retry
def discover_characters(directory: Path) -> dict[str, list[Path]]:
    if not directory.is_dir():
        raise ValueError(f"Character reference directory is missing: {directory}")
    groups: dict[str, list[Path]] = {}
    for path in sorted(directory.iterdir()):
        match = FILENAME.fullmatch(path.name)
        if path.is_file() and match:
            groups.setdefault(match[1], []).append(path)
    return groups


def preset_reference_pools(config, convert=True):
    characters = [c for c in config.preset.characters if c.get("imagePrefix")]
    if not characters:
        return ()
    directory = config.character_directory
    if directory is None or not directory.is_dir():
        raise ValueError(f"Character reference directory is missing: {directory}")
    if convert:
        convert_characters(directory, [c["imagePrefix"] for c in characters])
    pools = []
    for character in characters:
        pattern = re.compile(re.escape(character["imagePrefix"]) + r"[0-9]+\.(?i:jpg)")
        paths = sorted(p for p in directory.iterdir() if p.is_file() and pattern.fullmatch(p.name))
        if not paths:
            raise ValueError(
                f"No references for {character['id']}; "
                f"expected {character['imagePrefix']}[number].jpg"
            )
        pools.append(paths)
    return tuple(pools)


def reference_pools(config: Config):
    if config.preset is not None:
        return preset_reference_pools(config)
    if not config.flow.uses_references:
        return [], []
    if config.character_directory is None:
        # Keep existing configs with explicit image paths working.
        return tuple(
            [Path(getattr(config, role))] if role in config.flow.reference_roles else []
            for role in ("protagonist", "creature")
        )
    convert_characters(config.character_directory)
    groups = discover_characters(config.character_directory)
    pools = []
    for role, character_id in (("protagonist", config.protagonist), ("creature", config.creature)):
        if role not in config.flow.reference_roles:
            pools.append([])
            continue
        candidates = groups.get(str(character_id), [])
        if not candidates:
            raise ValueError(
                f"No references for {role} character {character_id} in "
                f"{config.character_directory}; expected {character_id}000.jpg, "
                f"{character_id}001.jpg, ..."
            )
        pools.append(candidates)
    return pools[0], pools[1]
