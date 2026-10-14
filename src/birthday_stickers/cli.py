"""Command-line entry point."""

import argparse
import logging
from pathlib import Path

from .comfy import ComfyClient
from .config import auth_headers, integer, load_config
from .http import HTTPClient
from .pipeline import validate_local


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
    modes.add_argument("--headless", action="store_true", help="Run without the Textual interface")
    parser.add_argument("--resume", metavar="SESSION", help="Resume a session in headless mode")
    parser.add_argument(
        "--enable-outpainting",
        action="store_true",
        help="Add outpainting to the resumed session, keeping generated originals",
    )
    args = parser.parse_args(argv)
    if args.enable_outpainting and not (args.headless and args.resume):
        parser.error("--enable-outpainting requires --headless --resume SESSION")
    if args.resume and not args.headless:
        parser.error("--resume requires --headless")
    if args.headless or args.dry_run or args.check:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
        )
    try:
        config = load_config(args.config)
        if args.count is not None:
            integer(args.count, "--count", 1)
            config.generation.count = args.count
        if args.dry_run or args.check:
            local = validate_local(config)
            print("Local files, character images, workflow mappings, and dimensions are valid.")
            if args.check:
                ComfyClient(config.comfy).preflight(local["graphs"][0])
                if config.generation.outpainting:
                    ComfyClient(config.outpainting_comfy, "outpainting").preflight(
                        local["graphs"][1]
                    )
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
                f"{config.final_size[0]}x{config.final_size[1]}; output: {config.output.directory}"
            )
        elif args.headless:
            from .control import RunControl
            from .sessions import SessionStore

            store = SessionStore(config.output.directory / "sessions")
            name = args.resume or store.create(config)["name"]
            if args.enable_outpainting:
                store.enable_outpainting(name, config)
            result = store.execute(name, RunControl())
            print(f"Session {name}: {result['status']}. Files: {store.directory(name)}")
            if result["status"] == "failed":
                logging.error("%s", result.get("error", "Generation failed"))
                return 1
        else:
            from .tui import StickerApp

            StickerApp(config).run()
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Saved artifacts remain; submitted ComfyUI jobs may still be running.")
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        logging.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
