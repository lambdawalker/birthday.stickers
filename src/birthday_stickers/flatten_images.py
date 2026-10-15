#!/usr/bin/env python3
"""
Flatten and rename images from subdirectories into the root directory.

Given a directory, recursively discovers all image files in its subdirectories
(and root), moves (or copies) them into the root directory, and renames them as
[Number].[imageextension] (e.g., 1.jpg, 2.png, 3.webp).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

# Common image file extensions (lowercase)
DEFAULT_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
    ".jfif",
    ".ico",
    ".ppm",
    ".pgm",
    ".pbm",
    ".heic",
    ".heif",
    ".avif",
    ".svg",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively find all images in subdirectories and place them into the root "
            "directory, renamed as [Number].[imageextension]."
        )
    )
    parser.add_argument(
        "directory",
        type=str,
        nargs="?",
        default=".",
        help="Path to the target directory (default: current working directory).",
    )
    parser.add_argument(
        "-s",
        "--start-number",
        type=int,
        default=1,
        help="Starting number for renamed images (default: 1).",
    )
    parser.add_argument(
        "-d",
        "--digits",
        type=int,
        default=0,
        help="Zero-pad numbers to this minimum width (e.g., 3 -> 001.jpg, default: 0 / unpadded).",
    )
    parser.add_argument(
        "-c",
        "--copy",
        action="store_true",
        help="Copy images instead of moving them (leaves originals in subdirectories).",
    )
    parser.add_argument(
        "--subdirs-only",
        action="store_true",
        help="Only process images in subdirectories, leaving existing root images untouched.",
    )
    parser.add_argument(
        "--keep-empty-dirs",
        action="store_true",
        help="Do not remove empty subdirectories after moving images.",
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default="",
        help=(
            "Comma-separated list of image extensions to match (e.g. 'jpg,png,webp'). "
            "Defaults to standard image formats."
        ),
    )
    parser.add_argument(
        "--sort-by",
        choices=["path", "name", "mtime"],
        default="path",
        help="Sort order for discovered images before numbering (default: path).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what files would be moved/renamed without modifying disk.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress per-file output and only print the final summary.",
    )
    return parser.parse_args(argv)


def get_supported_extensions(custom_extensions: str = "") -> set[str]:
    """Parse custom extensions string or return default supported extensions."""
    if not custom_extensions.strip():
        return set(DEFAULT_IMAGE_EXTENSIONS)
    exts = set()
    for item in custom_extensions.split(","):
        item = item.strip().lower()
        if item:
            if not item.startswith("."):
                item = f".{item}"
            exts.add(item)
    return exts


def find_images(
    root_dir: Path,
    extensions: set[str],
    subdirs_only: bool = False,
    sort_by: str = "path",
) -> list[Path]:
    """
    Find all matching image files inside root_dir recursively.
    """
    images: list[Path] = []
    root_dir = root_dir.resolve()

    for path in root_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue
        if subdirs_only and path.parent == root_dir:
            continue
        images.append(path)

    if sort_by == "name":
        images.sort(key=lambda p: (p.name.lower(), str(p)))
    elif sort_by == "mtime":
        images.sort(key=lambda p: (p.stat().st_mtime, str(p)))
    else:  # path
        images.sort(key=lambda p: str(p.relative_to(root_dir)).lower())

    return images


def remove_empty_directories(root_dir: Path, dry_run: bool = False) -> list[Path]:
    """
    Remove empty subdirectories inside root_dir (bottom-up).
    """
    removed: list[Path] = []
    root_dir = root_dir.resolve()

    for current_dir, _subdirs, _files in os.walk(root_dir, topdown=False):
        current_path = Path(current_dir)
        if current_path == root_dir:
            continue
        # Check if empty (no files and no subdirectories)
        try:
            if not any(current_path.iterdir()):
                if not dry_run:
                    current_path.rmdir()
                removed.append(current_path)
        except (OSError, PermissionError):
            pass

    return removed


def flatten_and_rename_images(
    directory: str | Path,
    start_number: int = 1,
    digits: int = 0,
    copy: bool = False,
    subdirs_only: bool = False,
    clean_empty_dirs: bool = True,
    extensions: set[str] | None = None,
    sort_by: str = "path",
    dry_run: bool = False,
    quiet: bool = False,
) -> list[tuple[Path, Path]]:
    """
    Flattens images from subdirectories into directory root and renames them to [Number].[ext].

    Returns a list of (source_path, destination_path) tuples.
    """
    root_dir = Path(directory).resolve()
    if not root_dir.exists():
        raise FileNotFoundError(f"Target directory does not exist: {root_dir}")
    if not root_dir.is_dir():
        raise NotADirectoryError(f"Target path is not a directory: {root_dir}")

    if extensions is None:
        extensions = DEFAULT_IMAGE_EXTENSIONS

    images = find_images(
        root_dir=root_dir,
        extensions=extensions,
        subdirs_only=subdirs_only,
        sort_by=sort_by,
    )

    if not images:
        if not quiet:
            print(f"No image files found in '{root_dir}'.")
        return []

    if not quiet:
        mode_str = "Copying" if copy else "Moving"
        dry_str = " [DRY RUN]" if dry_run else ""
        print(f"{mode_str} and renaming {len(images)} image(s) in '{root_dir}'{dry_str}:")

    plan: list[tuple[Path, Path]] = []
    for idx, src_path in enumerate(images, start=start_number):
        if digits > 0:
            formatted_num = f"{idx:0{digits}d}"
        else:
            formatted_num = str(idx)
        ext = src_path.suffix.lower()
        dst_name = f"{formatted_num}{ext}"
        dst_path = root_dir / dst_name
        plan.append((src_path, dst_path))

    if dry_run:
        for src, dst in plan:
            rel_src = src.relative_to(root_dir) if src.is_relative_to(root_dir) else src
            print(f"  {rel_src} -> {dst.name}")
        if clean_empty_dirs and not copy:
            empty_dirs = remove_empty_directories(root_dir, dry_run=True)
            if empty_dirs and not quiet:
                print(f"Would remove {len(empty_dirs)} empty directory(ies).")
        return plan

    if copy:
        # Direct copy
        for src, dst in plan:
            rel_src = src.relative_to(root_dir) if src.is_relative_to(root_dir) else src
            if not quiet:
                print(f"  {rel_src} -> {dst.name}")
            shutil.copy2(src, dst)
    else:
        # Move operation with two-phase staging to avoid collision when renaming
        session_id = uuid.uuid4().hex[:8]
        temp_moves: list[tuple[Path, Path]] = []

        for idx, (src, dst) in enumerate(plan):
            temp_name = f"._temp_flatten_{session_id}_{idx}_{dst.name}"
            temp_path = root_dir / temp_name
            shutil.move(str(src), str(temp_path))
            temp_moves.append((temp_path, dst))

        for temp_path, dst in temp_moves:
            if dst.exists() and dst != temp_path:
                dst.unlink()
            shutil.move(str(temp_path), str(dst))

        if not quiet:
            for src, dst in plan:
                rel_src = src.relative_to(root_dir) if src.is_relative_to(root_dir) else src
                print(f"  {rel_src} -> {dst.name}")

        if clean_empty_dirs:
            removed_dirs = remove_empty_directories(root_dir, dry_run=False)
            if removed_dirs and not quiet:
                print(f"Removed {len(removed_dirs)} empty directory(ies).")

    if not quiet:
        action_verb = "Copied" if copy else "Moved"
        print(f"Successfully {action_verb.lower()} {len(plan)} image(s) to '{root_dir}'.")

    return plan


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    exts = get_supported_extensions(args.extensions)
    try:
        flatten_and_rename_images(
            directory=args.directory,
            start_number=args.start_number,
            digits=args.digits,
            copy=args.copy,
            subdirs_only=args.subdirs_only,
            clean_empty_dirs=not args.keep_empty_dirs,
            extensions=exts,
            sort_by=args.sort_by,
            dry_run=args.dry_run,
            quiet=args.quiet,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
