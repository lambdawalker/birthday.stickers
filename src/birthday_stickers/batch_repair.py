"""Filter images with a vision LLM and repair glasses reflections with Qwen."""

import argparse
import copy
import hashlib
import json
import secrets
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock

import yaml

from .batch_outpaint import EXTENSIONS, checkpoint
from .comfy import ComfyClient
from .config import ComfyConfig, LLMConfig
from .control import RunControl, RunHalted
from .http import RejectedSubmission
from .images import decode_image, save_jpeg
from .llm import LLMClient
from .parallel import run_parallel
from .retry import file_retry, read_bytes, read_text, retry_scope, write_text
from .sessions import session_lock
from .wifi_watchdog import wifi_watchdog
from .workflows import apply_overrides, load_workflow

FLOW = Path(__file__).parent / "flows" / "glasses_repair"
PROMPTS = {
    "girl_prompt": "girl.md",
    "glasses_prompt": "glasses.md",
    "reflection_prompt": "reflection.md",
    "negative_prompt": "negative.md",
}


def validate(config):
    if Path(config["source"]).resolve() == Path(config["destination"]).resolve():
        raise ValueError("Source and destination must be different directories")
    for field in ("steps", "jpeg_quality"):
        if type(config[field]) is not int or config[field] < 1:
            raise ValueError(f"{field} must be a positive integer")
    if not 1 <= config["jpeg_quality"] <= 100 or not 0 < config["cfg"] <= 100:
        raise ValueError("JPEG quality must be 1–100 and CFG greater than 0 and at most 100")
    if type(config["recursive"]) is not bool:
        raise ValueError("recursive must be true or false")
    LLMConfig(**config["llm"])
    ComfyConfig(**config["comfy"])


def settings(path):
    path = Path(path).resolve()
    config = yaml.safe_load(read_text(path, encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Repair configuration must be a YAML mapping")
    for key, default in {"steps": 25, "cfg": 1.0, "jpeg_quality": 95, "recursive": False}.items():
        config.setdefault(key, default)
    for key in ("source", "destination"):
        config[key] = str((path.parent / config[key]).resolve())
    for key, filename in PROMPTS.items():
        config[key] = (
            str((path.parent / config[key]).resolve()) if key in config else str(FLOW / filename)
        )
    validate(config)
    return config


def decision(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ValueError("Classifier must return a JSON object with a boolean answer") from error
    if not isinstance(data, dict) or type(data.get("answer")) is not bool:
        raise ValueError("Classification uncertain or invalid; image left unmodified")
    if not isinstance(data.get("reason"), str) or not data["reason"].strip():
        raise ValueError("Classifier must include its visual reason")
    return data


def build_edit(template, config, prompt, upload, seed, prefix, negative):
    graph = copy.deepcopy(template)
    apply_overrides(graph, config.get("overrides", {}))
    graph["image"]["inputs"]["image"] = upload
    graph["459:474"]["inputs"].update(prompt=prompt, negative_prompt=negative, resolution=0)
    graph["459:458"]["inputs"].update(seed=seed, steps=config["steps"], cfg=config["cfg"])
    graph["461"]["inputs"]["filename_prefix"] = prefix
    return graph


def run_batch(config, control=None, report=lambda *args: None):
    validate(config)
    control = control or RunControl()
    source = Path(config["source"]).resolve()
    root = Path(config["destination"]).resolve()
    if not source.is_dir():
        raise ValueError(f"Source directory does not exist: {source}")
    paths = sorted(
        p
        for p in (source.rglob("*") if config["recursive"] else source.iterdir())
        if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.is_relative_to(root)
    )
    if not paths:
        raise ValueError("No supported images in source directory")
    names = set()
    for path in paths:
        name = path.relative_to(source).with_suffix(".jpg").as_posix().casefold()
        if name in names:
            raise ValueError(f"Output filename collision: {name}")
        names.add(name)
    for folder in ("results", "state", "prompts", "uploads", "logs/analysis", "logs/repair"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    counts = dict(described=0, rendered=0, skipped=0, flagged=0, failed=0, total=len(paths))
    lock = RLock()
    ready = Queue()
    done = Event()

    def advance(**changes):
        with lock:
            for key, value in changes.items():
                counts[key] += value
            report("progress", counts.copy())

    @file_retry
    def log(stage, text):
        with lock:
            with (root / "logs" / stage / "run.log").open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
            report("prompts_log" if stage == "analysis" else "outpainting_log", text)

    def failed(stage, path, error):
        advance(failed=1)
        log(stage, f"Failed {path.name}: {error}")

    with retry_scope(control.check, "repair"), session_lock(root):
        prompts = {key: read_text(Path(config[key]), encoding="utf-8") for key in PROMPTS}
        template = load_workflow(FLOW / "qwen.json")
        signature = json.dumps(
            {"prompts": prompts, "model": config["llm"]["model"]}, sort_keys=True
        )
        llm_config = LLMConfig(**{**config["llm"], "send_reference_images": True})
        comfy = ComfyClient(ComfyConfig(**config["comfy"]), "repair")
        advance()

        def analyze():
            with retry_scope(control.check, "prompts"):
                try:
                    for path in paths:
                        control.check()
                        try:
                            relative = path.relative_to(source).with_suffix(".jpg")
                            output = root / "results" / relative
                            # A complete, readable source-named output is the completion marker.
                            if output.exists():
                                try:
                                    decode_image(output)
                                except (OSError, ValueError):
                                    pass
                                else:
                                    advance(described=1, flagged=1, rendered=1)
                                    log("analysis", f"Already repaired: {relative}")
                                    continue
                            key = hashlib.sha256(
                                (
                                    str(relative)
                                    + hashlib.sha256(read_bytes(path)).hexdigest()
                                    + signature
                                ).encode()
                            ).hexdigest()
                            state = root / "state" / (key + ".json")
                            record = (
                                json.loads(read_text(state, encoding="utf-8"))
                                if state.exists()
                                else {"source": str(path), "seed": secrets.randbits(63)}
                            )
                            if record.pop("complete", False):
                                # Deleted results are explicitly regenerated with a fresh seed.
                                record.pop("prompt_id", None)
                                record.pop("request_id", None)
                                record["seed"] = secrets.randbits(63)
                                checkpoint(state, record)
                            client = None
                            for field, prompt_key in (
                                ("girl", "girl_prompt"),
                                ("glasses", "glasses_prompt"),
                            ):
                                if field not in record:
                                    control.check()
                                    client = client or LLMClient(
                                        llm_config,
                                        (path,),
                                        system_prompt=(
                                            "Inspect the supplied image. "
                                            "Follow the requested output format exactly."
                                        ),
                                    )
                                    log("analysis", f"Checking {field}: {path.name}")
                                    started = time.monotonic()
                                    record[field] = decision(client.complete(prompts[prompt_key]))
                                    checkpoint(state, record)
                                    log(
                                        "analysis",
                                        f"{field}: {record[field]['answer']} · "
                                        f"{time.monotonic() - started:.1f}s",
                                    )
                                if not record[field]["answer"]:
                                    record["flagged"] = False
                                    checkpoint(state, record)
                                    advance(described=1, skipped=1)
                                    log(
                                        "analysis",
                                        f"Skipped {path.name}: {record[field]['reason']}",
                                    )
                                    break
                            else:
                                record["flagged"] = True
                                checkpoint(state, record)
                                if "prompt" not in record:
                                    control.check()
                                    client = client or LLMClient(
                                        llm_config,
                                        (path,),
                                        system_prompt=(
                                            "Write a precise image edit instruction. "
                                            "Output only the edit prompt."
                                        ),
                                    )
                                    started = time.monotonic()
                                    record["prompt"] = client.complete(prompts["reflection_prompt"])
                                    checkpoint(state, record)
                                    log(
                                        "analysis",
                                        f"Edit prompt ready: {path.name} · "
                                        f"{time.monotonic() - started:.1f}s",
                                    )
                                classification = json.dumps(
                                    {k: record[k] for k in ("girl", "glasses")}, indent=2
                                )
                                write_text(
                                    root / "prompts" / (key + ".md"),
                                    f"# {relative}\n\n## Classification\n\n{classification}"
                                    f"\n\n## Edit prompt\n\n{record['prompt']}\n",
                                    encoding="utf-8",
                                )
                                advance(described=1, flagged=1)
                                control.check()
                                ready.put((path, output, key, state, record))
                        except RunHalted:
                            raise
                        except Exception as error:
                            failed("analysis", path, error)
                finally:
                    done.set()

        def render():
            with retry_scope(control.check, "repair"):
                while True:
                    control.check()
                    try:
                        job = ready.get(timeout=0.1)
                    except Empty:
                        if done.is_set() and ready.empty():
                            return
                        continue
                    path, output, key, state, record = job
                    try:
                        control.check()
                        started = time.monotonic()
                        log("repair", f"Fixing glasses: {path.name}")
                        if "prompt_id" not in record:
                            if record.get("request_id"):
                                record["prompt_id"] = comfy.submit(
                                    {}, record["request_id"], recover=True
                                )
                            else:
                                upload = root / "uploads" / (key + ".png")
                                file_retry(decode_image(path).save)(upload, format="PNG")
                                graph = build_edit(
                                    template,
                                    config,
                                    record["prompt"],
                                    comfy.upload(upload, "glasses-repair"),
                                    record["seed"],
                                    key,
                                    prompts["negative_prompt"],
                                )
                                comfy.preflight(graph)
                                write_text(
                                    root / "state" / (key + ".workflow.json"),
                                    json.dumps(graph),
                                    encoding="utf-8",
                                )
                                record["request_id"] = uuid.uuid4().hex
                                checkpoint(state, record)
                                try:
                                    record["prompt_id"] = comfy.submit(graph, record["request_id"])
                                except RejectedSubmission:
                                    record.pop("request_id", None)
                                    checkpoint(state, record)
                                    raise
                            checkpoint(state, record)
                        result = comfy.wait_image(record["prompt_id"], "461", control.check)
                        output.parent.mkdir(parents=True, exist_ok=True)
                        save_jpeg(decode_image(result), output, config["jpeg_quality"], 0, "white")
                        record.update(complete=True, output=str(output))
                        checkpoint(state, record)
                        advance(rendered=1)
                        log("repair", f"Saved {output.name} · {time.monotonic() - started:.1f}s")
                    except RunHalted:
                        raise
                    except Exception as error:
                        failed("repair", path, error)

        run_parallel(analyze, render, control)
        return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("glasses-repair.yaml"))
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
        validate(config)
        with wifi_watchdog(Path(config["destination"]) / "logs", not args.no_wifi):
            if args.headless:
                return int(
                    bool(run_batch(config, report=lambda k, v: print(v, flush=True))["failed"])
                )
            from .outpaint_tui import OutpaintApp

            OutpaintApp(config, repair=True).run()
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
