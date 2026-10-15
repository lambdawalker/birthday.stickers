"""Reconcile deleted outputs without confusing unfinished jobs with deleted results."""

import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .artifacts import result_path, stage_log

SEED_MODULUS = 2**63


def next_seed(previous: int) -> int:
    return (previous + 1 + secrets.randbelow(SEED_MODULUS - 1)) % SEED_MODULUS


def reconcile_outputs(config, path: Path, meta: dict, save) -> None:
    root, name = config.output.directory, meta["sticker"]
    image = result_path(root, "images", name)
    extended = result_path(root, "outpainting", name)
    image_expected = (
        meta.get("image_saved")
        or meta.get("intermediate_saved")
        or meta.get("status") == "complete"
    )
    missing_image = image_expected and not image.is_file()
    missing_extended = meta.get("outpainting_saved") and not extended.is_file()
    if missing_image or missing_extended:
        stages = ("qwen", "flux") if missing_image else ("flux",)
        for stage in stages:
            if meta.get(f"{stage}_submission_pending") and not meta.get(f"{stage}_prompt_id"):
                raise ValueError(
                    f"{name}: resolve the unknown {stage} submission before regenerating"
                )
        history = {
            key: meta[key]
            for stage in stages
            for key in (f"{stage}_seed", f"{stage}_prompt_id", f"{stage}_server")
            if key in meta
        }
        # An invalidated downstream job may still be running. Retain its identity
        # until the engine confirms it is terminal before submitting replacement work.
        if (
            missing_image
            and meta.get("flux_prompt_id")
            and not meta.get("outpainting_saved")
            and not meta.get("flux_drained")
        ):
            meta.setdefault("retired_jobs", []).append(
                {
                    "prompt_id": meta["flux_prompt_id"],
                    "server": meta.get(
                        "flux_server", config.outpainting_comfy.base_url.rstrip("/")
                    ),
                }
            )
        for stage in stages:
            meta[f"{stage}_seed"] = next_seed(meta.get(f"{stage}_seed", 0))
            for suffix in ("prompt_id", "submission_pending", "server"):
                meta.pop(f"{stage}_{suffix}", None)
        meta.pop("flux_drained", None)
        meta.pop("outpaint_skipped", None)
        meta.pop("outpainting_prompt", None)
        meta.pop("padding", None)
        meta.pop("finished_at", None)
        meta.pop("error", None)
        meta.pop("failed_stage", None)
        meta["outpainting_saved"] = False
        if missing_image:
            meta["image_saved"] = False
            meta["intermediate_saved"] = False
            if extended.is_file():
                meta["archive_outpainting"] = f"outpainting/superseded/{name}-{uuid4().hex}.jpg"
        meta["status"] = (
            "prompts_ready"
            if missing_image
            else ("image_ready" if config.generation.outpainting else "complete")
        )
        reason = (
            "Generated image deleted; invalidate generation and outpainting"
            if missing_image
            else "Outpainted image deleted"
        )
        meta.setdefault("regenerations", []).append(
            {
                "time": datetime.now(UTC).isoformat(),
                "reason": reason,
                "previous": history,
                "new_seeds": {stage: meta[f"{stage}_seed"] for stage in stages},
            }
        )
        # Commit invalidation and the archive intent before moving any output.
        save(path, meta)
        stage_log(
            root,
            "images" if missing_image else "outpainting",
            name,
            event="regeneration_requested",
            reason=reason,
        )
    if destination := meta.get("archive_outpainting"):
        archive = root / destination
        archive.parent.mkdir(parents=True, exist_ok=True)
        if extended.exists():
            if archive.exists():
                raise ValueError(f"Cannot archive outdated output: destination exists: {archive}")
            extended.replace(archive)
        meta.pop("archive_outpainting")
        save(path, meta)
