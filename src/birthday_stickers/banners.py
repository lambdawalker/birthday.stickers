"""Compose one woodland/Viking print banner per immediate input subfolder."""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

ASSETS = Path(__file__).parent / "assets" / "banner_frame"
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
# Remove generation padding; the original PNGs remain available for reuse.
CROPS = {
    "left": (258, 2, 510, 1533),
    "right": (510, 10, 755, 1525),
    "top": (8, 112, 1975, 680),
    "divider": (0, 195, 1881, 640),
    "bottom": (7, 310, 1976, 725),
}


def natural_key(path: Path) -> tuple:
    return tuple((1, int(p)) if p.isdigit() else (0, p.casefold())
                 for p in re.split(r"(\d+)", path.name)) + ((0, path.name),)


def images_in(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in EXTENSIONS), key=natural_key)


def frame_piece(name: str, size: tuple[int, int]) -> Image.Image:
    with Image.open(ASSETS / f"{name}.png") as source:
        piece = source.convert("RGBA").crop(CROPS[name])
    if name in {"left", "right"}:
        # The generated side art has a translucent halo. Remove that halo,
        # retaining solid foliage and bark, before compositing over pictures.
        piece.putalpha(piece.getchannel("A").point(lambda a: 255 if a >= 180 else 0))
    if name == "top":
        # Match width without flattening the carved wood and hanging leaves.
        size = (size[0], round(piece.height * size[0] / piece.width))
    return piece.resize(size, Image.Resampling.LANCZOS)


def render_banner(paths: list[Path], destination: Path, *, image_size: int = 1536,
                  border: int = 192, dpi: int = 300, fit: str = "contain",
                  max_megapixels: float = 120) -> tuple[int, int]:
    if not paths:
        raise ValueError("No images to render")
    if image_size < 1 or border < 1 or dpi < 1:
        raise ValueError("Image size, border and DPI must be positive")
    if fit not in {"contain", "cover"}:
        raise ValueError("Fit must be contain or cover")
    width = image_size + 2 * border
    height = len(paths) * image_size + (len(paths) + 1) * border
    if width * height > max_megapixels * 1_000_000:
        raise ValueError(f"Banner would be {width} x {height} pixels; reduce --image-size "
                         "or raise --max-megapixels (requires more RAM)")
    canvas = Image.new("RGBA", (width, height), "#bca384")
    frame = Image.new("RGBA", canvas.size)
    overlap = max(1, border // 2)
    left = frame_piece("left", (border + overlap, image_size + 2 * overlap))
    right = frame_piece("right", (border + overlap, image_size + 2 * overlap))
    for index, path in enumerate(paths):
        y = border + index * (image_size + border)
        with Image.open(path) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGBA")
            if fit == "cover":
                picture = ImageOps.fit(oriented, (image_size, image_size),
                                       method=Image.Resampling.LANCZOS)
            else:
                picture = ImageOps.contain(oriented, (image_size, image_size),
                                           method=Image.Resampling.LANCZOS)
            square = Image.new("RGBA", (image_size, image_size), "white")
            square.alpha_composite(picture, ((image_size - picture.width) // 2,
                                            (image_size - picture.height) // 2))
        # Reflect a real strip instead of stretching a single row into streaks.
        bleed = min(overlap, image_size)
        canvas.alpha_composite(
            ImageOps.flip(square.crop((0, 0, image_size, bleed)))
            .resize((image_size, overlap)), (border, y - overlap))
        canvas.alpha_composite(
            ImageOps.flip(square.crop((0, image_size - bleed, image_size, image_size)))
            .resize((image_size, overlap)), (border, y + image_size))
        canvas.alpha_composite(square, (border, y))
        frame.alpha_composite(left, (0, y - overlap))
        frame.alpha_composite(right, (width - right.width, y - overlap))
    # Horizontal beams cover the vertical joins. The final canvas is opaque
    # and rectangular; only the interior foliage overlaps the photographs.
    frame.alpha_composite(frame_piece("top", (width, border + overlap)), (0, 0))
    for index in range(1, len(paths)):
        y = index * (image_size + border)
        frame.alpha_composite(frame_piece("divider", (width, border + 2 * overlap)),
                               (0, y - overlap))
    frame.alpha_composite(frame_piece("bottom", (width, border + overlap)),
                           (0, height - border - overlap))
    # Cast the assembled silhouette once, below every frame component, so
    # foliage casts a shadow onto images without darkening overlapping beams.
    radius = max(1, border / 16)
    offset = max(1, round(border / 32))
    shadow = Image.new("L", canvas.size)
    shadow.paste(frame.getchannel("A"), (offset, offset))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius)).point(lambda a: round(a * 0.38))
    canvas.paste((0, 0, 0, 255), (0, 0), shadow)
    canvas.alpha_composite(frame)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(suffix=".jpg", dir=destination.parent)
    os.close(descriptor)
    try:
        canvas.convert("RGB").save(
            temporary, format="JPEG", quality=95, optimize=True, progressive=True,
            subsampling=2, dpi=(dpi, dpi))
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return width, height


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("print/banners/inputs"))
    parser.add_argument("--output-dir", type=Path, default=Path("print/banners/outputs"))
    parser.add_argument("--image-size", type=positive_int, default=1536,
                        help="square image opening in pixels (default: 1536)")
    parser.add_argument("--border", type=positive_int, default=None,
                        help="border width in pixels (default: image size / 8)")
    parser.add_argument("--dpi", type=positive_int, default=300)
    parser.add_argument("--fit", choices=("contain", "cover"), default="contain",
                        help="contain preserves whole image; cover center-crops to square")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-megapixels", type=positive_int, default=120,
                        help="maximum output megapixels per banner (default: 120)")
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error(f"Input directory does not exist: {args.input_dir}")
    root = args.input_dir.resolve()
    output = args.output_dir.resolve()
    if output == root or root in output.parents:
        parser.error("Output directory must be outside the input directory")
    folders = sorted((p for p in root.iterdir() if p.is_dir()), key=natural_key)
    if not folders:
        print(f"No subfolders found in {root}. Add images in inputs/<banner-name>/.")
        return 1
    errors = 0
    for folder in folders:
        paths = images_in(folder)
        if not paths:
            print(f"SKIP {folder.name}: no supported images")
            continue
        destination = output / f"{folder.name}.jpg"
        if destination.exists() and not args.overwrite:
            print(f"SKIP {destination}: already exists (use --overwrite to rebuild)")
            continue
        try:
            width, height = render_banner(
                paths, destination, image_size=args.image_size,
                border=args.border or max(1, args.image_size // 8),
                dpi=args.dpi, fit=args.fit, max_megapixels=args.max_megapixels)
            print(f"OK {destination}: {len(paths)} images, {width} x {height} px, "
                  f"{width / args.dpi * 2.54:.1f} x {height / args.dpi * 2.54:.1f} cm "
                  f"at {args.dpi} DPI")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            errors += 1
            print(f"ERROR {folder.name}: {exc}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
