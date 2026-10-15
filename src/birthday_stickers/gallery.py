"""Content-addressed gallery exports and imports; originals are never moved."""

import argparse
import hashlib
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from .retry import file_retry

PIPELINES = ("image_generation", "outpainting")
EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tiff"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
 pipeline TEXT NOT NULL, hash TEXT NOT NULL, filename TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(pipeline, hash));
CREATE TABLE IF NOT EXISTS sources (
 pipeline TEXT NOT NULL, hash TEXT NOT NULL, source TEXT NOT NULL,
 PRIMARY KEY(pipeline, hash, source));
CREATE TABLE IF NOT EXISTS reviewers (
 name TEXT PRIMARY KEY, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reviews (
 name TEXT NOT NULL, pipeline TEXT NOT NULL, hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('accepted','rejected')),
 favorite INTEGER NOT NULL DEFAULT 0, tshirt INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(name,pipeline,hash));
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pipeline TEXT NOT NULL,
 hash TEXT NOT NULL, previous TEXT, undone INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS requests (
 name TEXT NOT NULL, token TEXT NOT NULL, PRIMARY KEY(name,token));
"""


class Gallery:
    def __init__(self, root=Path("gallery")):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for pipeline in PIPELINES:
            (self.root / pipeline).mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "gallery.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @file_retry
    def add(self, source, pipeline="image_generation"):
        if pipeline not in PIPELINES:
            raise ValueError("Unknown pipeline")
        source = Path(source).resolve()
        # Hash the exact staged bytes, so a source replaced during export cannot
        # produce a filename that disagrees with its contents.
        fd, temporary = tempfile.mkstemp(prefix=".import-", dir=self.root / pipeline)
        temporary = Path(temporary)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as target, source.open("rb") as incoming:
                while block := incoming.read(1024 * 1024):
                    digest.update(block)
                    target.write(block)
            with Image.open(temporary) as image:
                suffix = EXTENSIONS.get(image.format)
                image.verify()
            if suffix is None:
                raise ValueError(f"Unsupported image format: {source.name}")
            key = digest.hexdigest()
            filename = key + suffix
            destination = self.root / pipeline / filename
            # Serialize exporters; the file is installed before the manifest row.
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                exists = db.execute(
                    "SELECT 1 FROM images WHERE pipeline=? AND hash=?", (pipeline, key)
                ).fetchone()
                if not destination.exists():
                    os.replace(temporary, destination)
                db.execute(
                    "INSERT OR IGNORE INTO images(pipeline,hash,filename) VALUES(?,?,?)",
                    (pipeline, key, filename),
                )
                db.execute(
                    "INSERT OR IGNORE INTO sources VALUES(?,?,?)", (pipeline, key, str(source))
                )
            return not bool(exists)
        finally:
            temporary.unlink(missing_ok=True)

    def import_files(self, files, pipeline):
        result = {"added": 0, "skipped": 0, "errors": []}
        for path in files:
            try:
                added = self.add(path, pipeline)
                result["added" if added else "skipped"] += 1
            except (OSError, ValueError) as error:
                result["errors"].append(f"{path}: {error}")
        return result

    def export_sessions(self, sessions):
        root = Path(sessions)
        if not root.is_dir():
            raise ValueError(f"Sessions directory does not exist: {root}")
        return {
            pipeline: self.import_files(sorted(root.glob(f"*/{pipeline}/results/*.jpg")), pipeline)
            for pipeline in PIPELINES
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a flat image folder into the gallery.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--gallery", type=Path, default=Path("gallery"))
    parser.add_argument("--pipeline", choices=PIPELINES, default=PIPELINES[0])
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        parser.error("Source must be an existing directory")
    suffixes = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
    files = sorted(p for p in args.source.iterdir() if p.is_file() and p.suffix.lower() in suffixes)
    result = Gallery(args.gallery).import_files(files, args.pipeline)
    print(f"Added {result['added']}; already present {result['skipped']}.")
    for error in result["errors"]:
        print(error)
    print(f"Gallery: {args.gallery.resolve()}")
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
