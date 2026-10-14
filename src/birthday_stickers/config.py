"""Strict, early validation of user-editable YAML settings."""

import math
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from PIL import ImageColor


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key_env: str | None = None
    timeout: float = 300
    temperature: float = 0.9
    refinement_temperature: float = 0.3
    max_tokens: int = 4096
    token_limit_field: str = "max_tokens"
    send_reference_images: bool = False
    extra_body: dict = field(default_factory=dict)


@dataclass
class ComfyConfig:
    base_url: str
    api_key_env: str | None = None
    request_timeout: float = 60
    job_timeout: float = 1800
    poll_interval: float = 2
    get_retries: int = 3
    retry_delay: float = 1


@dataclass
class GenerationConfig:
    count: int = 10
    width: int = 1024
    height: int = 1024
    seed: int | None = None


@dataclass
class OutputConfig:
    directory: Path = Path("output")
    width: int = 1536
    height: int = 1536
    jpeg_quality: int = 95
    jpeg_subsampling: int = 0
    jpeg_background: str = "#ffffff"
    feathering: int = 24
    preserve_center: bool = True


@dataclass
class WorkflowConfig:
    path: Path
    nodes: dict
    overrides: dict = field(default_factory=dict)


@dataclass
class Config:
    path: Path
    llm: LLMConfig
    comfy: ComfyConfig
    generation: GenerationConfig
    output: OutputConfig
    protagonist: Path | str
    creature: Path | str
    planning: Path
    refinement: Path
    guide: Path
    qwen: WorkflowConfig
    flux: WorkflowConfig
    character_directory: Path | None = None


def integer(value, name: str, low: int, high: int = 2**63 - 1) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")


def number(value, name: str, low: float, inclusive: bool = False) -> None:
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or (value < low if inclusive else value <= low)
    ):
        raise ValueError(f"{name} must be a finite number {'>=' if inclusive else '>'} {low}")


def section(data: dict, name: str, cls):
    raw = data.get(name, {})
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be a YAML mapping")
    unknown = set(raw) - {item.name for item in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown {name} setting(s): {', '.join(sorted(unknown))}")
    try:
        return cls(**raw)
    except TypeError as error:
        raise ValueError(f"Invalid {name}: {error}") from error


def local_path(base: Path, value) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("File paths must be nonempty strings")
    return (base / Path(value).expanduser()).resolve()


def validate_url(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an http(s) URL")
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(f"{name} must be an http(s) URL without credentials, query, or fragment")
    try:
        _ = parts.port  # Access validates a malformed or out-of-range port.
    except ValueError as error:
        raise ValueError(f"{name}: {error}") from error


def auth_headers(environment_name: str | None) -> dict[str, str]:
    if environment_name is None:
        return {}
    value = os.environ.get(environment_name, "")
    if not value:
        raise ValueError(f"Set environment variable {environment_name} or set api_key_env to null")
    return {"Authorization": f"Bearer {value}"}


def load_config(path: Path) -> Config:
    path = path.expanduser().resolve()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")
    expected = {"llm", "comfy", "generation", "output", "characters", "prompts", "workflows"}
    if set(data) - expected:
        raise ValueError(f"Unknown config sections: {set(data) - expected}")
    llm = section(data, "llm", LLMConfig)
    comfy = section(data, "comfy", ComfyConfig)
    generation = section(data, "generation", GenerationConfig)
    output = section(data, "output", OutputConfig)
    for name, item in (("llm", llm), ("comfy", comfy)):
        validate_url(item.base_url, name + ".base_url")
        if item.api_key_env is not None and (
            not isinstance(item.api_key_env, str) or not item.api_key_env.strip()
        ):
            raise ValueError(f"{name}.api_key_env must be an environment-variable name or null")
    if not isinstance(llm.model, str) or not llm.model.strip():
        raise ValueError("llm.model must be a model ID")
    if llm.token_limit_field not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("llm.token_limit_field must be max_tokens or max_completion_tokens")
    if type(llm.send_reference_images) is not bool or type(output.preserve_center) is not bool:
        raise ValueError("send_reference_images and preserve_center must be true or false")
    reserved = {"model", "messages", "stream", "max_tokens", "max_completion_tokens", "temperature"}
    if not isinstance(llm.extra_body, dict) or reserved & llm.extra_body.keys():
        raise ValueError("llm.extra_body must be a mapping without reserved request fields")
    for key in ("temperature", "refinement_temperature"):
        number(getattr(llm, key), "llm." + key, 0, True)
    number(llm.timeout, "llm.timeout", 0)
    integer(llm.max_tokens, "llm.max_tokens", 1)
    for key in ("request_timeout", "job_timeout", "poll_interval"):
        number(getattr(comfy, key), "comfy." + key, 0)
    number(comfy.retry_delay, "comfy.retry_delay", 0, True)
    integer(comfy.get_retries, "comfy.get_retries", 0, 20)
    integer(generation.count, "generation.count", 1)
    if generation.seed is not None:
        integer(generation.seed, "generation.seed", 0)
    for key in ("width", "height"):
        integer(getattr(generation, key), "generation." + key, 32, 16384)
        if getattr(generation, key) % 32:
            raise ValueError(f"generation.{key} must be a multiple of 32")
        integer(getattr(output, key), "output." + key, 1, 16384)
        if getattr(output, key) < getattr(generation, key):
            raise ValueError(f"output.{key} is smaller than generation.{key}")
    integer(output.jpeg_quality, "output.jpeg_quality", 1, 100)
    integer(output.jpeg_subsampling, "output.jpeg_subsampling", 0, 2)
    integer(output.feathering, "output.feathering", 0, 8192)
    try:
        color = ImageColor.getrgb(output.jpeg_background)
        if len(color) != 3:
            raise ValueError("use an opaque RGB background")
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"output.jpeg_background: {error}") from error
    base = path.parent
    output.directory = local_path(base, output.directory)
    paths = {}
    characters = data.get("characters")
    if isinstance(characters, dict) and "directory" in characters:
        if set(characters) != {"directory", "protagonist", "creature"}:
            raise ValueError("characters must contain directory, protagonist, and creature")
        paths["character_directory"] = local_path(base, characters["directory"])
        for role in ("protagonist", "creature"):
            value = characters[role]
            if not isinstance(value, str) or len(value) != 1 or not "A" <= value <= "Z":
                raise ValueError(f"characters.{role} must be one character ID from A-Z")
            paths[role] = value
        if paths["protagonist"] == paths["creature"]:
            raise ValueError("Protagonist and creature must use different character IDs")
    for group, keys in (
        ("characters", ("protagonist", "creature")),
        ("prompts", ("planning", "refinement", "guide")),
    ):
        if group == "characters" and "character_directory" in paths:
            continue
        raw = data.get(group)
        if not isinstance(raw, dict) or set(raw) != set(keys):
            raise ValueError(f"{group} must contain exactly: {', '.join(keys)}")
        paths.update({key: local_path(base, raw[key]) for key in keys})
    workflows = data.get("workflows")
    if not isinstance(workflows, dict) or set(workflows) != {"qwen", "flux"}:
        raise ValueError("workflows must contain qwen and flux")
    for key, roles in (
        ("qwen", {"protagonist", "creature", "encoder", "latent", "sampler", "output"}),
        ("flux", {"image", "prompt", "pad", "sampler", "conditioning", "output"}),
    ):
        item = section(workflows, key, WorkflowConfig)
        item.path = local_path(base, item.path)
        if not isinstance(item.nodes, dict) or set(item.nodes) != roles:
            raise ValueError(f"workflows.{key}.nodes must contain: {', '.join(sorted(roles))}")
        item.nodes = {role: str(value) for role, value in item.nodes.items()}
        if not isinstance(item.overrides, dict):
            raise ValueError(f"workflows.{key}.overrides must be a mapping")
        paths[key] = item
    return Config(path, llm, comfy, generation, output, **paths)
