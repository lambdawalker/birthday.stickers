"""Flow registry, packaged assets, and flow-first/common-fallback resolution."""

import copy
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
NAMES = ("scene_preset",)


def canonical_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Flow name must be a string")
    if name not in NAMES:
        raise ValueError(f"Unknown flow {name!r}; choose {', '.join(NAMES)}")
    return name


def resolve_asset(name: str, flow_directory: Path, common_directory: Path) -> Path:
    """A flow's own file takes precedence over the shared file with the same name."""
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Flow asset names must be relative paths without '..'")
    for directory in (flow_directory, common_directory):
        path = directory / relative
        if path.is_file():
            return path.resolve()
    raise ValueError(
        f"Missing flow asset {name!r}; searched {flow_directory} and {common_directory}"
    )


def asset_path(base: Path, value) -> Path:
    """Resolve explicit paths without redirects to retired assets."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("File paths must be nonempty strings")
    return (base / Path(value).expanduser()).resolve()


@dataclass(frozen=True)
class Flow:
    name: str
    roles: tuple[str, ...] | None = None

    @property
    def uses_references(self) -> bool:
        return bool(self.roles) if self.roles is not None else self.module.USES_REFERENCES

    @property
    def reference_roles(self) -> tuple[str, ...]:
        if self.roles is not None:
            return self.roles
        return self.module.REFERENCE_ROLES

    @property
    def directory(self) -> Path:
        return ROOT / self.name

    @property
    def module(self):
        from importlib import import_module

        return import_module(f"{__name__}.{self.name}")

    def build_graph(self, config, template, prompt, references, seed, prefix):
        return self.module.build_graph(config, template, prompt, references, seed, prefix)

    def system_prompt(self, refined, has_images):
        return self.module.system_prompt(refined, has_images)


def get_flow(name: str) -> Flow:
    return Flow(canonical_name(name))


def expand_config(data: dict, base: Path) -> dict:
    """Apply flow defaults; session snapshots remain self-contained."""
    result = copy.deepcopy(data)
    if "flow" not in result:
        return result
    flow = get_flow(result.pop("flow"))
    generation = result.setdefault("generation", {})
    if not isinstance(generation, dict):
        raise ValueError("generation must be a YAML mapping")
    if "type" in generation and canonical_name(generation["type"]) != flow.name:
        raise ValueError("flow and generation.type disagree")
    generation["type"] = flow.name
    defaults = yaml.safe_load((ROOT / "common/flow.yaml").read_text(encoding="utf-8"))
    specific = yaml.safe_load((flow.directory / "flow.yaml").read_text(encoding="utf-8"))
    for section in ("prompts", "workflows"):
        values = {**defaults.get(section, {}), **specific.get(section, {})}
        overrides = result.get(section, {})
        if not isinstance(overrides, dict):
            raise ValueError(f"{section} must be a YAML mapping")
        for key, value in overrides.items():
            if section == "workflows" and isinstance(value, dict) and key in values:
                original = values[key]
                merged = {**original, **value}
                for field in ("nodes", "overrides"):
                    if field in value and isinstance(value[field], dict):
                        merged[field] = {**original.get(field, {}), **value[field]}
                if isinstance(value.get("overrides"), dict):
                    for node, inputs in value["overrides"].items():
                        if isinstance(inputs, dict):
                            merged["overrides"][node] = {
                                **original.get("overrides", {}).get(node, {}),
                                **inputs,
                            }
                values[key] = merged
            else:
                values[key] = value
        for key, value in values.items():
            if section == "prompts" and value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"prompts.{key} must be a filename or null")
                if key not in overrides or Path(value).name == value:
                    values[key] = str(resolve_asset(value, flow.directory, ROOT / "common"))
                else:
                    values[key] = str(asset_path(base, value))
            elif section == "workflows" and isinstance(value, dict) and "path" in value:
                explicit = isinstance(overrides.get(key), dict) and "path" in overrides[key]
                value["path"] = str(
                    asset_path(base, value["path"])
                    if explicit
                    else resolve_asset(value["path"], flow.directory, ROOT / "common")
                )
        result[section] = values
    return result
