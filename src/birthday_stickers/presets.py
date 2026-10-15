"""Scene presets, strict templates, and repeatable scene selections."""

import copy
import json
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .retry import read_text

ROOT = Path(__file__).parent / "presets"
FILES = (
    "characters.yaml",
    "activities.yaml",
    "venues.yaml",
    "props.yaml",
    "image_style.md",
    "general_direction.md",
)


def mapping(path):
    try:
        value = yaml.safe_load(read_text(path, encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a mapping")
    return value


def validate_props(value, location):
    if not isinstance(value, dict):
        raise ValueError(f"{location}: props must be a mapping")

    def walk(item):
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            if not item:
                raise ValueError(f"{location}: random lists cannot be empty")
            for child in item:
                walk(child)
        elif not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{location}: props must contain strings, numbers, lists or mappings")

    walk(value)


def catalog(path, key):
    rows = mapping(path).get(key)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: {key} must be a nonempty list")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{path}: each entry must be a mapping")
        if "id" not in row and isinstance(row.get("name"), str):
            row["id"] = re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-")
        for field in ("id", "name", "description"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{path}: every entry needs {field}")
        if row["id"] in seen:
            raise ValueError(f"{path}: duplicate id {row['id']}")
        seen.add(row["id"])
        validate_props(row.get("props", {}), path)
        for field in ("tags", "requiredCharacters", "venueTags"):
            if field in row and (
                not isinstance(row[field], list) or any(not isinstance(v, str) for v in row[field])
            ):
                raise ValueError(f"{path}: {field} must be a list of strings")
    return rows


@dataclass
class Preset:
    directory: Path
    characters: list
    activities: list
    venues: list
    props: dict
    image_style: str
    direction: str

    @property
    def reference_roles(self):
        return tuple(c["id"] for c in self.characters if c.get("imagePrefix"))

    def compatible(self, activity):
        tags = set(activity.get("venueTags", []))
        return [v for v in self.venues if not tags or tags.intersection(v.get("tags", []))]


def load_preset(directory):
    directory = Path(directory).resolve()
    signature = tuple(
        (p.stat().st_mtime_ns, p.stat().st_size) for p in (directory / name for name in FILES)
    )
    return copy.deepcopy(_load_preset(directory, signature))


@lru_cache(maxsize=16)
def _load_preset(directory, signature):
    characters = mapping(directory / "characters.yaml").get("characters")
    if not isinstance(characters, list) or not characters:
        raise ValueError("characters.yaml: characters must be a nonempty list")
    seen = set()
    for character in characters:
        if not isinstance(character, dict):
            raise ValueError("Each character must be a mapping")
        ident = character.get("id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", ident):
            raise ValueError(
                "Character IDs must start with a letter and contain letters, digits or _"
            )
        if ident in seen:
            raise ValueError(f"Duplicate character id: {ident}")
        seen.add(ident)
        if not isinstance(character.get("name"), str) or not character["name"].strip():
            raise ValueError(f"{ident}: name is required")
        if not character.get("description") and not character.get("imagePrefix"):
            raise ValueError(f"{ident}: description or imagePrefix is required")
        for key in ("description", "imagePrefix"):
            if key in character and (
                not isinstance(character[key], str) or not character[key].strip()
            ):
                raise ValueError(f"{ident}: {key} must be nonempty text")
        if character.get("imagePrefix") and not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", character["imagePrefix"]
        ):
            raise ValueError(f"{ident}: imagePrefix must be a filename prefix, not a path")
    prefixes = [c["imagePrefix"] for c in characters if c.get("imagePrefix")]
    for index, prefix in enumerate(prefixes):
        for other in prefixes[index + 1 :]:
            if (
                prefix == other
                or re.fullmatch(re.escape(prefix) + r"[0-9]+", other)
                or (re.fullmatch(re.escape(other) + r"[0-9]+", prefix))
            ):
                raise ValueError(f"Overlapping image prefixes: {prefix}, {other}")
    if len(prefixes) > 10:
        raise ValueError("Qwen supports at most 10 reference images per scene")
    activities = catalog(directory / "activities.yaml", "activities")
    venues = catalog(directory / "venues.yaml", "venues")
    props = mapping(directory / "props.yaml").get("props", {})
    validate_props(props, "props.yaml")
    result = Preset(
        directory,
        characters,
        activities,
        venues,
        props,
        read_text(directory / "image_style.md", encoding="utf-8").strip(),
        read_text(directory / "general_direction.md", encoding="utf-8").strip(),
    )
    if not result.image_style or not result.direction:
        raise ValueError("image_style.md and general_direction.md cannot be empty")
    # Incompatible activities may coexist in a reusable catalog; only eligible ones are sampled.
    eligible = [a for a in activities if set(a.get("requiredCharacters", [])).issubset(seen)]
    if not eligible:
        raise ValueError("No activities match the preset's characters")
    result.activities = eligible
    for activity in eligible:
        venues = result.compatible(activity)
        if not venues:
            raise ValueError(f"Activity {activity['id']} has no compatible venue")
        # Render each activity and each venue before any paid/server operations.
        render_scene(result, activity, venues[0], 0)
    for venue in result.venues:
        render_scene(result, eligible[0], venue, 0)
    return result


def render_scene(preset, activity, venue, seed, references=()):
    rng = random.Random(seed)
    selections = []
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    env.globals.clear()

    def choose(items):
        if not isinstance(items, (list, tuple)) or not items:
            raise ValueError("random requires a nonempty list")
        value = copy.deepcopy(rng.choice(items))
        selections.append(value)
        return value

    env.filters["random"] = choose

    def render(text, context, origin):
        try:
            return env.from_string(text).render(context).strip()
        except (TemplateError, ValueError, TypeError) as error:
            raise ValueError(f"{origin}: {error}") from error

    chars = {c["id"]: copy.deepcopy(c) for c in preset.characters}
    image_index = 0
    for character in chars.values():
        if character.get("imagePrefix"):
            image_index += 1
            character["imageTag"] = f"<image{image_index}>"
        else:
            character["imageTag"] = ""
    context = {"characters": chars, "props": preset.props, "globalProps": preset.props}
    venue = copy.deepcopy(venue)
    venue.setdefault("props", {})
    context["venue"] = venue
    venue["description"] = render(
        venue["description"], {**context, "props": venue["props"]}, f"venues.yaml:{venue['id']}"
    )
    activity = copy.deepcopy(activity)
    activity.setdefault("props", {})
    context["activity"] = activity
    activity["description"] = render(
        activity["description"],
        {**context, "props": activity["props"]},
        f"activities.yaml:{activity['id']}",
    )
    style = render(preset.image_style, context, "image_style.md")
    blocks = []
    ref_by_role = {r["role"]: r for r in references}
    for character in chars.values():
        source = character["imageTag"]
        lines = [f"Character {character['id']}: {character['name']}"]
        if source:
            lines.append(
                f"Identity reference: {source}. Preserve identity and recognizable features."
            )
            if character["id"] in ref_by_role:
                lines.append(f"Reference file: {Path(ref_by_role[character['id']]['path']).name}")
        else:
            lines.append("Source: text description only; no reference image.")
        if character.get("description"):
            lines.append(character["description"])
        blocks.append("\n".join(lines))
    direction = render(preset.direction, context, "general_direction.md")
    return {
        "prompt": style + "\n\n" + "\n\n".join(blocks) + "\n\n" + direction,
        "activity": activity,
        "venue": venue,
        "choices": selections,
        "selection_seed": seed,
        "characters": list(chars.values()),
    }


def select_scene(preset, metadata_directory, references=()):
    used = set()
    ids = {a["id"] for a in preset.activities}
    paths = sorted(
        metadata_directory.glob("stiker-*.json"), key=lambda p: int(p.stem.split("-")[-1])
    )
    for path in paths:
        saved = json.loads(read_text(path, encoding="utf-8"))
        activity = saved.get("scene_selection", {}).get("activity", {}).get("id")
        if activity in ids:
            used.add(activity)
            if used == ids:
                used.clear()
    rng = random.SystemRandom()
    activity = rng.choice([a for a in preset.activities if a["id"] not in used])
    venue = rng.choice(preset.compatible(activity))
    return render_scene(preset, activity, venue, rng.randrange(2**63), references)


def discover_presets(config_path):
    local = config_path.parent / "presets"
    found = {
        path.name: path
        for path in ROOT.iterdir()
        if path.is_dir() and (path / "characters.yaml").is_file()
    }
    if local.is_dir():
        found.update(
            {
                path.name: path
                for path in local.iterdir()
                if path.is_dir() and (path / "characters.yaml").is_file()
            }
        )
    return dict(sorted(found.items()))


def with_preset(config, directory):
    """Use a preset while preserving launcher endpoints and generation/output settings."""
    from .config import WorkflowConfig
    from .flows import expand_config

    result = copy.deepcopy(config)
    result.preset = load_preset(directory)
    result.generation.type = "scene_preset"
    defaults = expand_config({"flow": "scene_preset"}, config.path.parent)
    for key in ("planning", "refinement", "guide", "activities"):
        value = defaults["prompts"].get(key)
        setattr(result, key, Path(value) if value else None)
    workflow = defaults["workflows"]["qwen"]
    if config.preset is None:
        result.qwen = WorkflowConfig(
            Path(workflow["path"]), workflow["nodes"], workflow["overrides"]
        )
    if result.flux is None:
        workflow = defaults["workflows"]["flux"]
        result.flux = WorkflowConfig(
            Path(workflow["path"]), workflow["nodes"], workflow["overrides"]
        )
    if result.character_directory is None and result.preset.reference_roles:
        result.character_directory = (config.path.parent / "assets/characters").resolve()
    return result
