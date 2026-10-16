"""Describe existing images with a vision LLM, then outpaint them with Qwen."""

import argparse
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import yaml

from .comfy import ComfyClient
from .config import ComfyConfig, LLMConfig
from .control import RunControl, RunHalted
from .http import RejectedSubmission
from .images import decode_image, finish_outpaint, plan_padding, save_jpeg
from .llm import LLMClient
from .retry import file_retry, read_bytes, read_text, replace, retry_scope, write_text
from .sessions import session_lock
from .wifi_watchdog import wifi_watchdog
from .workflows import build_outpaint, load_workflow

COMMON = Path(__file__).parent / "flows" / "common"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def settings(path):
    path = Path(path).resolve()
    data = yaml.safe_load(read_text(path, encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Outpainting configuration must be a YAML mapping")
    for key, default in {
        "width": 1536,
        "height": 1536,
        "feathering": 24,
        "steps": 25,
        "cfg": 1.0,
        "jpeg_quality": 95,
        "recursive": False,
        "preserve_center": True,
    }.items():
        data.setdefault(key, default)
    for key in ("source", "destination"):
        if not data.get(key):
            raise ValueError(f"Missing {key} directory")
        data[key] = str((path.parent / data[key]).resolve())
    if data["source"] == data["destination"]:
        raise ValueError("Source and destination must be different directories")
    for key in ("width", "height", "steps", "jpeg_quality"):
        if isinstance(data[key], bool) or not isinstance(data[key], int) or data[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if data["jpeg_quality"] > 100 or not 0 <= data["feathering"] <= 8192:
        raise ValueError("JPEG quality must be 1–100 and feathering 0–8192")
    if not 0 < float(data["cfg"]) <= 100:
        raise ValueError("CFG must be greater than 0 and at most 100")
    for key in ("recursive", "preserve_center"):
        if not isinstance(data[key], bool):
            raise ValueError(f"{key} must be true or false")
    LLMConfig(**data["llm"])
    ComfyConfig(**data["comfy"])
    for key, default in (
        ("description_prompt", "describe-outpainting.md"),
        ("outpainting_prompt", "outpainting.md"),
        ("negative_prompt", "negative.md"),
    ):
        data[key] = (
            str((path.parent / data[key]).resolve()) if key in data else str(COMMON / default)
        )
    return data


@file_retry
def checkpoint(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
    replace(temporary, path)


def run_batch(config, control=None, report=lambda *args: None):
    control = control or RunControl()
    root = Path(config["destination"])
    source_root = Path(config["source"])
    if not source_root.is_dir():
        raise ValueError(f"Source directory does not exist: {source_root}")
    paths = sorted(
        p
        for p in (source_root.rglob("*") if config["recursive"] else source_root.iterdir())
        if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.is_relative_to(root)
    )
    if not paths:
        raise ValueError("No supported images found in the source directory")
    for folder in ("results", "prompts", "state", "logs", "uploads"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    original_report = report

    @file_retry
    def log_line(message):
        with (root / "logs" / "run.log").open("a", encoding="utf-8") as log:
            log.write(message + "\n")

    def report(kind, value):
        if kind == "log":
            log_line(value)
        original_report(kind, value)

    with retry_scope(control.check, "outpainting"), session_lock(root):
        prompts = {
            key: read_text(Path(config[key]), encoding="utf-8")
            for key in ("description_prompt", "outpainting_prompt", "negative_prompt")
        }
        binding = yaml.safe_load((COMMON / "flow.yaml").read_text())["workflows"]["flux"]
        template = load_workflow(COMMON / binding["path"])
        overrides = {**config.get("overrides", {})}
        overrides["114"] = {
            **overrides.get("114", {}),
            "steps": config["steps"],
            "cfg": config["cfg"],
        }
        comfy = ComfyClient(ComfyConfig(**config["comfy"]), "outpainting")
        llm_config = LLMConfig(**{**config["llm"], "send_reference_images": True})
        signature = json.dumps(
            {"config": config, "prompts": prompts, "workflow": template}, sort_keys=True
        )
        counts = {"described": 0, "rendered": 0, "failed": 0, "total": len(paths)}
        report("progress", counts.copy())
        for path in paths:
            control.check()
            started = time.monotonic()
            try:
                digest = hashlib.sha256(read_bytes(path)).hexdigest()
                key = hashlib.sha256(
                    (str(path.relative_to(source_root)) + digest + signature).encode()
                ).hexdigest()
                name = path.stem + "-" + key[:16]
                state_path = root / "state" / (name + ".json")
                output = root / "results" / (name + ".jpg")
                record = (
                    json.loads(read_text(state_path))
                    if state_path.exists()
                    else {"source": str(path), "seed": secrets.randbits(63)}
                )
                if record.get("complete") and output.exists():
                    counts["described"] += 1
                    counts["rendered"] += 1
                    report("log", f"Already complete: {path.name}")
                    report("progress", counts.copy())
                    continue
                source = decode_image(path)
                padding = plan_padding(source.size, (config["width"], config["height"]))
                if "description" not in record:
                    report("log", f"Describing {path.name} · {llm_config.base_url}")
                    step = time.monotonic()
                    record["description"] = LLMClient(
                        llm_config,
                        (path,),
                        system_prompt=(
                            "You describe supplied images accurately for outpainting. "
                            "Follow the user's instructions and output only the requested prompt."
                        ),
                    ).complete(prompts["description_prompt"])
                    checkpoint(state_path, record)
                    report("log", f"Description ready · {time.monotonic() - step:.1f}s")
                counts["described"] += 1
                prompt = (
                    prompts["outpainting_prompt"].strip() + "\n\nScene: " + record["description"]
                )
                write_text(
                    root / "prompts" / (name + ".md"),
                    f"# {path.name}\n\n## Description\n\n{record['description']}"
                    f"\n\n## Outpainting prompt\n\n{prompt}\n",
                    encoding="utf-8",
                )
                report("progress", counts.copy())
                control.check()
                if source.size == padding.target:
                    final = source
                else:
                    if "prompt_id" not in record:
                        if record.get("request_id"):
                            record["prompt_id"] = comfy.submit(
                                {}, record["request_id"], recover=True
                            )
                        else:
                            upload_path = root / "uploads" / (name + ".png")
                            file_retry(source.save)(upload_path, format="PNG")
                            uploaded = comfy.upload(upload_path, "outpaint-source")
                            graph = build_outpaint(
                                template,
                                binding["nodes"],
                                overrides,
                                prompt,
                                uploaded,
                                padding,
                                config["feathering"],
                                record["seed"],
                                name,
                                negative=prompts["negative_prompt"],
                                preserve_center=config["preserve_center"],
                            )
                            comfy.preflight(graph)
                            write_text(
                                root / "state" / (name + ".workflow.json"),
                                json.dumps(graph, indent=2),
                            )
                            record["request_id"] = uuid.uuid4().hex
                            checkpoint(state_path, record)
                            try:
                                record["prompt_id"] = comfy.submit(graph, record["request_id"])
                            except RejectedSubmission:
                                record.pop("request_id", None)
                                checkpoint(state_path, record)
                                raise
                        checkpoint(state_path, record)
                    report("log", f"Outpainting {path.name} · {comfy.config.base_url}")
                    step = time.monotonic()
                    result = comfy.wait_image(
                        record["prompt_id"], binding["nodes"]["output"], control.check
                    )
                    final = finish_outpaint(
                        decode_image(result), source, padding, config["feathering"], False
                    )
                    report("log", f"Outpainting ready · {time.monotonic() - step:.1f}s")
                save_jpeg(final, output, config["jpeg_quality"], 0, "white")
                record.update(complete=True, output=str(output), padding=asdict(padding))
                checkpoint(state_path, record)
                counts["rendered"] += 1
                message = f"Saved {output.name} · total {time.monotonic() - started:.1f}s"
                write_text(root / "logs" / (name + ".log"), message + "\n")
                report("log", message)
            except RunHalted:
                raise
            except Exception as error:
                counts["failed"] += 1
                report("log", f"Failed {path.name}: {error}")
                write_text(
                    root
                    / "logs"
                    / (hashlib.sha256(str(path).encode()).hexdigest()[:16] + ".error.log"),
                    str(error),
                )
            report("progress", counts.copy())
        return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("outpaint.yaml"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-wifi", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = settings(args.config)
        for key in ("source", "destination"):
            if getattr(args, key):
                config[key] = str(getattr(args, key).resolve())
        if config["source"] == config["destination"]:
            raise ValueError("Source and destination must be different directories")
        with wifi_watchdog(Path(config["destination"]) / "logs", not args.no_wifi):
            if args.headless:
                result = run_batch(config, report=lambda kind, value: print(value, flush=True))
                return 1 if result["failed"] else 0
            from .outpaint_tui import OutpaintApp

            OutpaintApp(config).run()
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
