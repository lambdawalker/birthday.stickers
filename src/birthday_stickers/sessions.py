"""Durable session manifests, input snapshots, and exclusive process ownership."""

import json
import os
import shutil
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import RLock

import yaml

from .config import Config, integer, load_config
from .control import RunControl, RunHalted
from .pipeline import run, scene_records, validate_local, write_json


def now() -> str:
    return datetime.now().astimezone().isoformat()


@contextmanager
def session_lock(directory: Path):
    """OS locks are automatically released after crashes, including on Windows."""
    with (directory / "session.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("This session is already running in another process.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.errors: list[str] = []

    def next_name(self) -> str:
        prefix = datetime.now().strftime("%Y-%m-%d")
        counter = 1
        while (self.root / f"{prefix}-{counter:03d}").exists():
            counter += 1
        return f"{prefix}-{counter:03d}"

    def directory(self, name: str) -> Path:
        if (
            not name
            or Path(name).name != name
            or name in (".", "..")
            or "/" in name
            or "\\" in name
        ):
            raise ValueError("Invalid session name")
        directory = (self.root / name).resolve()
        if directory.parent != self.root:
            raise ValueError("Session must be inside the sessions directory")
        return directory

    def read(self, name: str) -> dict:
        data = json.loads((self.directory(name) / "session.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("name") != name:
            raise ValueError(f"Invalid session manifest: {name}")
        integer(data.get("count"), "session count", 1)
        return data

    def list_incomplete(self) -> list[dict]:
        self.errors = []
        result = []
        for path in sorted(self.root.glob("*/session.json"), reverse=True):
            try:
                item = self.read(path.parent.name)
                if item["status"] != "complete":
                    result.append(item)
            except (OSError, ValueError, KeyError) as error:
                self.errors.append(f"{path.parent.name}: {error}")
        return result

    def create(self, config: Config, count: int | None = None) -> dict:
        target = config.generation.count if count is None else count
        integer(target, "Sticker count", 1)
        validate_local(config)
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            name = self.next_name()
            directory = self.directory(name)
            try:
                directory.mkdir()
                break
            except FileExistsError:
                continue
        # Snapshot small planning inputs; character images remain in their live source folder.
        try:
            snapshots = directory / "inputs"
            snapshots.mkdir()
            prompts = {}
            for key, path in (
                ("planning", config.planning),
                ("refinement", config.refinement),
                ("guide", config.guide),
                ("activities", config.activities),
            ):
                if path is not None:
                    destination = snapshots / f"{key}{path.suffix}"
                    shutil.copyfile(path, destination)
                    prompts[key] = str(destination.relative_to(directory))
            workflows = {}
            for key, workflow in (("qwen", config.qwen), ("flux", config.flux)):
                if workflow is None or (key == "flux" and not config.generation.outpainting):
                    continue
                destination = snapshots / f"{key}.json"
                shutil.copyfile(workflow.path, destination)
                workflows[key] = {
                    **asdict(workflow),
                    "path": str(destination.relative_to(directory)),
                }
            characters = {}
            if config.character_directory is not None:
                characters = {
                    "directory": str(config.character_directory),
                    "protagonist": config.protagonist,
                    "creature": config.creature,
                }
            elif config.generation.type == "viking_duo":
                characters = {
                    role: str(getattr(config, role)) for role in ("protagonist", "creature")
                }
            data = {
                "llm": asdict(config.llm),
                "comfy": asdict(config.comfy),
                "generation": {**asdict(config.generation), "count": target},
                "output": {**asdict(config.output), "directory": "."},
                "characters": characters,
                "prompts": prompts,
                "workflows": workflows,
            }
            (directory / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
            manifest = {
                "version": 1,
                "generation_type": config.generation.type,
                "outpainting": config.generation.outpainting,
                "name": name,
                "count": target,
                "completed": 0,
                "prompts_completed": 0,
                "status": "ready",
                "created_at": now(),
                "updated_at": now(),
                "stage": "Ready to generate",
                "sticker": "",
            }
            write_json(directory / "session.json", manifest)
            return manifest
        except Exception:
            shutil.rmtree(directory)
            raise

    def execute(
        self,
        name: str,
        control: RunControl,
        notify: Callable[[dict], None] = lambda _: None,
    ) -> dict:
        directory = self.directory(name)
        with session_lock(directory):
            manifest = self.read(name)
            if manifest["status"] == "complete":
                return manifest

            update_lock = RLock()

            def update(**changes):
                with update_lock:
                    manifest.update(**changes, updated_at=now())
                    write_json(directory / "session.json", manifest)
                    notify(dict(manifest))

            def worker_update(lane, done, sticker, stage):
                key = "prompts_completed" if lane == "prompts" else "completed"
                update(
                    **{key: done, f"{lane}_stage": stage, f"{lane}_sticker": sticker},
                    sticker=sticker,
                    stage=stage,
                )

            try:
                config = load_config(directory / "config.yaml")
                records = scene_records(directory)
                completed = sum(m.get("status") == "complete" for _, m in records)
                prompts_completed = sum(
                    "refined_scene" in m or m.get("status") == "complete" for _, m in records
                )
                update(
                    status="running",
                    completed=completed,
                    prompts_completed=prompts_completed,
                    prompts_stage="starting",
                    images_stage="starting",
                    error="",
                    prompts_sticker="",
                    images_sticker="",
                    stage="Checking services",
                )
                run(
                    config,
                    resume=True,
                    control=control,
                    worker_progress=worker_update,
                )
                update(
                    status="complete",
                    completed=manifest["count"],
                    prompts_completed=manifest["count"],
                    prompts_stage="done",
                    images_stage="done",
                    stage="All stickers saved",
                )
            except RunHalted as error:
                update(
                    status=error.status,
                    stage="Progress saved",
                    prompts_stage=error.status,
                    images_stage=error.status,
                )
            except KeyboardInterrupt:
                update(status="stopped", stage="Progress saved")
                raise
            except Exception as error:
                update(
                    status="failed",
                    error=str(error),
                    stage="Needs attention",
                    prompts_stage="stopped",
                    images_stage="stopped",
                )
            return manifest
