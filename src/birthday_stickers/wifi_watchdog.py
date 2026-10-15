"""Run the optional Windows watchdog separately and always reap it on shutdown."""

import logging
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def wifi_watchdog(log_directory: Path, enabled=True):
    process = None
    log = None
    try:
        if enabled and sys.platform == "win32":
            try:
                log_directory.mkdir(parents=True, exist_ok=True)
                log = (log_directory / "wifi.log").open("ab")
                process = subprocess.Popen(
                    [sys.executable, "-m", "birthday_stickers.keep_wifi_connected", "--background"],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as error:
                logging.warning("Wi-Fi watchdog could not start: %s", error)
        yield
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if log is not None:
            log.close()
