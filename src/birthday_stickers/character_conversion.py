"""Convert live character references without overwriting existing JPEGs."""

import hashlib
import json
import re
from pathlib import Path
from threading import RLock

from .images import save_jpeg
from .retry import file_lock, file_retry, read_bytes, read_text, replace, write_text

SUPPORTED_EXTENSIONS = {
    ".png",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
    ".jpeg",
    ".jfif",
    ".ico",
    ".ppm",
    ".pgm",
    ".pbm",
}
PATTERN = re.compile(r"([A-Z])([0-9]+)\.[^.]+$")
_lock = RLock()


@file_retry
def convert_characters(directory: Path):
    """Retain originals; a durable conversion record prevents duplicate imports."""
    records = directory / ".jpg-conversions"
    records.mkdir(exist_ok=True)
    with _lock, file_lock(records / "conversion.lock"):
        for source in sorted(directory.iterdir()):
            match = PATTERN.fullmatch(source.name)
            if (
                not source.is_file()
                or not match
                or source.suffix.lower() not in SUPPORTED_EXTENSIONS
            ):
                continue
            data = read_bytes(source)
            digest = hashlib.sha256(data).hexdigest()
            # Each source/content has its own receipt, so adding files is incremental.
            records = directory / ".jpg-conversions"
            records.mkdir(exist_ok=True)
            receipt = records / (hashlib.sha256(source.name.encode()).hexdigest() + ".json")
            saved = json.loads(read_text(receipt, encoding="utf-8")) if receipt.exists() else {}
            if saved.get("sha256") == digest:
                target = directory / saved["target"]
                if saved.get("complete") and target.is_file():
                    continue
            else:
                label, number = match.groups()
                target = directory / f"{label}{number}.jpg"
                used = {p.name.lower() for p in directory.iterdir()}
                if target.name.lower() in used:
                    largest = max(
                        (
                            int(m[2])
                            for p in directory.iterdir()
                            if (m := PATTERN.fullmatch(p.name)) and m[1] == label
                        ),
                        default=-1,
                    )
                    target = directory / f"{label}{largest + 1:0{len(number)}d}.jpg"
                # Exclusive reservation also protects against another process creating a JPG.
                try:
                    with target.open("xb"):
                        pass
                except FileExistsError:
                    continue
                saved = {"sha256": digest, "target": target.name, "complete": False}
                temporary = receipt.with_suffix(".tmp")
                write_text(temporary, json.dumps(saved), encoding="utf-8")
                replace(temporary, receipt)
            save_jpeg(data, target, 95, 0, "white")
            saved["complete"] = True
            temporary = receipt.with_suffix(".tmp")
            write_text(temporary, json.dumps(saved), encoding="utf-8")
            replace(temporary, receipt)
