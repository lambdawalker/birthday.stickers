"""Launch services in separate uv environments, using only the standard library.

Invoke directly with Python: running this through `uv run` would first sync the
shared environment and defeat the Windows executable-lock protection.
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

SERVICES = {
    "repair": "birthday_stickers.batch_repair",
    "outpaint": "birthday_stickers.batch_outpaint",
    "generate": "birthday_stickers.cli",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a service in its own Python environment")
    parser.add_argument("service", choices=SERVICES)
    parser.add_argument(
        "arguments", nargs=argparse.REMAINDER, help="Arguments passed to the service"
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv is required; install it and make sure it is on PATH")
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(root / ".venvs" / args.service)
    env.pop("VIRTUAL_ENV", None)
    arguments = args.arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    command = [
        uv,
        "run",
        "--project",
        str(root),
        "python",
        "-m",
        SERVICES[args.service],
        *arguments,
    ]
    try:
        return subprocess.call(command, env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
