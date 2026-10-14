#!/usr/bin/env python3
"""
Convert all images in a target folder to JPEG (.jpg) format and delete the original files.
Supports handling of transparency (composited against a background color), EXIF orientation,
and safe atomic writing to prevent data loss.
"""

import argparse
import os
import sys
from pathlib import Path
from PIL import Image, ImageColor, ImageOps

# Common image extensions to process
SUPPORTED_EXTENSIONS = {
    ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif",
    ".jpeg", ".jfif", ".ico", ".ppm", ".pgm", ".pbm"
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert all images in a folder to .jpg format and delete original files."
    )
    parser.add_argument(
        "folder",
        type=str,
        nargs="?",
        default=".",
        help="Path to the directory containing images (default: current directory)."
    )
    parser.add_argument(
        "-q", "--quality",
        type=int,
        default=95,
        help="JPEG compression quality from 1 to 100 (default: 95)."
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Scan subdirectories recursively."
    )
    parser.add_argument(
        "-b", "--background",
        type=str,
        default="#FFFFFF",
        help="Hex color or name for background when flattening transparent images (default: #FFFFFF / white)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what files will be converted and deleted without modifying anything."
    )
    parser.add_argument(
        "--keep-originals",
        action="store_true",
        help="Keep the original image files instead of deleting them."
    )
    return parser.parse_args()


def convert_image_to_jpg(
    file_path: Path,
    quality: int = 95,
    bg_color: tuple = (255, 255, 255),
    dry_run: bool = False,
    delete_original: bool = True
) -> bool:
    """
    Converts a single image file to .jpg and deletes the original if successful.
    Returns True if converted/handled successfully, False otherwise.
    """
    dest_path = file_path.with_suffix(".jpg")

    # If already a lowercase .jpg file, verify if it's already a JPEG image
    if file_path.suffix == ".jpg" and file_path.exists():
        # Already has exact .jpg extension
        print(f"[-] Skipping (already .jpg): {file_path.name}")
        return True

    if dry_run:
        print(f"[DRY-RUN] Would convert: {file_path.name} -> {dest_path.name} (and delete {file_path.name})")
        return True

    temp_path = file_path.parent / f".tmp_{file_path.stem}_{os.getpid()}.jpg"

    try:
        with Image.open(file_path) as img:
            # Respect EXIF rotation tags (e.g. from mobile cameras)
            img = ImageOps.exif_transpose(img)

            # Handle alpha channel / transparency for JPEG compatibility
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, bg_color)
                # Paste image on background using alpha channel as mask
                bg.paste(img, mask=img.split()[3])
                rgb_img = bg
            elif img.mode != "RGB":
                rgb_img = img.convert("RGB")
            else:
                rgb_img = img

            # Write to a temporary file first for safety
            rgb_img.save(temp_path, "JPEG", quality=quality, optimize=True)

        # Ensure temp file was created properly
        if not temp_path.exists() or temp_path.stat().st_size == 0:
            raise RuntimeError("Generated file was empty or not created.")

        # If dest_path exists and is different from original (e.g. name conflict), replace it
        if temp_path != dest_path:
            temp_path.replace(dest_path)

        # Delete original file only after destination is safely written
        if delete_original and file_path.resolve() != dest_path.resolve():
            file_path.unlink()
            print(f"[✓] Converted: {file_path.name} -> {dest_path.name} (original deleted)")
        else:
            print(f"[✓] Converted: {file_path.name} -> {dest_path.name}")

        return True

    except Exception as e:
        print(f"[!] Error processing {file_path.name}: {e}", file=sys.stderr)
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False


def main():
    args = parse_args()
    target_dir = Path(args.folder).resolve()

    if not target_dir.is_dir():
        print(f"Error: Directory '{target_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    try:
        bg_rgb = ImageColor.getrgb(args.background)
    except ValueError:
        print(f"Error: Invalid background color '{args.background}'. Using white.", file=sys.stderr)
        bg_rgb = (255, 255, 255)

    # Discover candidate files
    if args.recursive:
        file_candidates = [p for p in target_dir.rglob("*") if p.is_file()]
    else:
        file_candidates = [p for p in target_dir.iterdir() if p.is_file()]

    # Filter to image candidates (excluding existing lowercase .jpg files)
    image_files = []
    for file_path in file_candidates:
        ext = file_path.suffix.lower()
        # Include known image extensions or uppercase .JPG
        if ext in SUPPORTED_EXTENSIONS or (ext == ".jpg" and file_path.suffix != ".jpg"):
            image_files.append(file_path)

    if not image_files:
        print(f"No non-JPG images found in {target_dir}.")
        return

    print(f"Found {len(image_files)} image(s) to convert in {target_dir}...")
    if args.dry_run:
        print("Running in DRY-RUN mode. No files will be modified.\n")

    success_count = 0
    fail_count = 0

    for file_path in image_files:
        success = convert_image_to_jpg(
            file_path=file_path,
            quality=args.quality,
            bg_color=bg_rgb,
            dry_run=args.dry_run,
            delete_original=not args.keep_originals
        )
        if success:
            success_count += 1
        else:
            fail_count += 1

    print("\n" + "=" * 40)
    print(f"Summary: {success_count} succeeded, {fail_count} failed, total {len(image_files)}.")
    print("=" * 40)


if __name__ == "__main__":
    main()
