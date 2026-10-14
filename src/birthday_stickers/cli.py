"""Command-line entry point."""

import argparse
import logging
from pathlib import Path

from .comfy import ComfyClient
from .config import auth_headers, integer, load_config
from .http import HTTPClient
from .pipeline import run, validate_local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan, render, and outpaint birthday sticker scenes."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config.yaml"), help="YAML configuration"
    )
    parser.add_argument("--count", type=int, help="Override generation.count for this invocation")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run", action="store_true", help="Validate files/workflows without network calls"
    )
    modes.add_argument(
        "--check", action="store_true", help="Also check LLM model and ComfyUI nodes/models"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        config = load_config(args.config)
        if args.count is not None:
            integer(args.count, "--count", 1)
            config.generation.count = args.count
        if args.dry_run or args.check:
            local = validate_local(config)
            print("Local files, character images, workflow mappings, and dimensions are valid.")
            if args.check:
                ComfyClient(config.comfy).preflight(*local["graphs"])
                llm = HTTPClient(
                    config.llm.base_url, config.llm.timeout, auth_headers(config.llm.api_key_env)
                )
                models = llm.json("GET", "/models")
                ids = [item.get("id") for item in models.get("data", []) if isinstance(item, dict)]
                if config.llm.model not in ids:
                    raise ValueError(
                        f"LLM model {config.llm.model!r} is unavailable. Server lists: {ids}"
                    )
                print("LLM model and ComfyUI nodes/model files are available. No images generated.")
            print(
                f"Ready for {config.generation.count} scene(s); final size "
                f"{config.output.width}x{config.output.height}; output: {config.output.directory}"
            )
        else:
            results = run(config)
            print(f"Saved {len(results)} sticker(s) to {config.output.directory / 'final'}")
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Saved artifacts remain; submitted ComfyUI jobs may still be running.")
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        logging.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
