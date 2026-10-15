# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow>=11,<13"]
# ///
"""Standalone dark-subject detector and centered cropper; no project imports or services."""

import argparse
import json
import math
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


def detect_dragon(image, threshold=145, seed_threshold=80, close_radius=2, max_side=1200):
    """Select a connected dark region supported by very dark pixels (8-neighbor)."""
    small = image.copy()
    small.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    width, height = small.size
    r, g, b = small.split()
    darkest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = darkest.point(lambda value: 255 if value <= threshold else 0)
    seeds = darkest.point(lambda value: 255 if value <= seed_threshold else 0).tobytes()
    if close_radius:
        kernel = 2 * close_radius + 1
        mask = mask.filter(ImageFilter.MaxFilter(kernel)).filter(ImageFilter.MinFilter(kernel))
    pixels = bytearray(mask.tobytes())
    components = []
    minimum = max(8, round(width * height * 0.00005))
    for start in range(len(pixels)):
        if not pixels[start]:
            continue
        pixels[start] = 0
        queue = deque([start])
        members = []
        seed_count = 0
        left = right = start % width
        top = bottom = start // width
        while queue:
            index = queue.popleft()
            members.append(index)
            y, x = divmod(index, width)
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            seed_count += bool(seeds[index])
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = ny * width + nx
                    if pixels[neighbor]:
                        pixels[neighbor] = 0
                        queue.append(neighbor)
        if seed_count >= minimum:
            touches_edge = sum((left == 0, top == 0, right == width - 1, bottom == height - 1))
            components.append(
                (touches_edge, seed_count, (left, top, right + 1, bottom + 1), members)
            )
    if not components:
        raise ValueError(
            "No substantial dark subject found. Try increasing --threshold or --seed-threshold."
        )
    # Reject surrounding frames, without rejecting a tail that reaches one edge.
    candidates = [item for item in components if item[0] < 3] or components
    edge, count, bounds, members = max(candidates, key=lambda item: item[1])
    selected = bytearray(width * height)
    anchor = bounds
    group = []
    # Wings/ears can be disconnected by highlights. Include components whose
    # boxes overlap the main subject box, but do not grow recursively into scenery.
    for item in candidates:
        other = item[2]
        if (
            other[0] <= anchor[2] + 3
            and other[2] >= anchor[0] - 3
            and other[1] <= anchor[3] + 3
            and other[3] >= anchor[1] - 3
        ):
            group.append(item)
    bounds = (
        min(item[2][0] for item in group),
        min(item[2][1] for item in group),
        max(item[2][2] for item in group),
        max(item[2][3] for item in group),
    )
    edge = any(item[0] for item in group)
    for item in group:
        for index in item[3]:
            selected[index] = 255
    selected = Image.frombytes("L", small.size, bytes(selected))
    sx, sy = image.width / width, image.height / height
    box = (
        math.floor(bounds[0] * sx),
        math.floor(bounds[1] * sy),
        math.ceil(bounds[2] * sx),
        math.ceil(bounds[3] * sy),
    )
    notes = []
    if edge:
        notes.append(
            "Selected subject touches the image edge; its silhouette may already be clipped."
        )
    if (
        len(candidates) > 1
        and sorted((item[1] for item in candidates), reverse=True)[1] > count * 0.5
    ):
        notes.append("Multiple substantial dark objects found; check the detection preview.")
    return box, selected.resize(image.size, Image.Resampling.NEAREST), notes


def centered_box(box, padding=0.15, aspect=1.0):
    left, top, right, bottom = box
    width = (right - left) * (1 + 2 * padding)
    height = (bottom - top) * (1 + 2 * padding)
    width, height = max(width, height * aspect), max(height, width / aspect)
    width, height = math.ceil(width), math.ceil(height)
    x = round((left + right - width) / 2)
    y = round((top + bottom - height) / 2)
    return x, y, x + width, y + height


def crop_centered(image, box, background="white"):
    """Pad rather than shifting the crop when its centered bounds cross an image edge."""
    left, top, right, bottom = box
    result = Image.new("RGB", (right - left, bottom - top), background)
    intersection = (max(0, left), max(0, top), min(image.width, right), min(image.height, bottom))
    if intersection[2] > intersection[0] and intersection[3] > intersection[1]:
        result.paste(image.crop(intersection), (intersection[0] - left, intersection[1] - top))
    return result


def ratio(value):
    try:
        first, second = value.split(":")
        result = float(first) / float(second)
        if not math.isfinite(result) or not 0.1 <= result <= 10:
            raise ValueError()
        return result
    except (ValueError, ZeroDivisionError) as error:
        raise argparse.ArgumentTypeError("Use W:H, between 1:10 and 10:1") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, help="Output JPEG/PNG (default: IMAGE.centered.jpg)")
    parser.add_argument(
        "--threshold",
        type=int,
        default=145,
        help="Maximum RGB channel for connected dark regions (default: 145)",
    )
    parser.add_argument(
        "--seed-threshold",
        type=int,
        default=80,
        help="Maximum RGB channel for very dark seed pixels (default: 80)",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=0.15,
        help="Space on each side, as a fraction of the detected box (default: 0.15)",
    )
    parser.add_argument(
        "--aspect", type=ratio, help="Crop aspect W:H; default square, or --size ratio"
    )
    parser.add_argument(
        "--size",
        type=int,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        help="Optional resized output dimensions",
    )
    parser.add_argument(
        "--background", default="white", help="Padding color outside source image (default: white)"
    )
    parser.add_argument(
        "--close-radius",
        type=int,
        default=2,
        help="Close small mask gaps in detection pixels (default: 2)",
    )
    parser.add_argument(
        "--max-side", type=int, default=1200, help="Maximum detection resolution (default: 1200)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Replace existing derived outputs; never the input"
    )
    args = parser.parse_args(argv)
    if not 0 <= args.seed_threshold <= args.threshold <= 255:
        parser.error("Require 0 <= seed-threshold <= threshold <= 255")
    if not math.isfinite(args.padding) or not 0 <= args.padding <= 2:
        parser.error("padding must be between 0 and 2")
    if not 0 <= args.close_radius <= 10 or not 64 <= args.max_side <= 4096:
        parser.error("close-radius must be 0..10 and max-side 64..4096")
    if args.size and any(not 1 <= value <= 16384 for value in args.size):
        parser.error("size dimensions must be 1..16384")
    aspect = args.aspect or (args.size[0] / args.size[1] if args.size else 1.0)
    if args.size and args.aspect and not math.isclose(aspect, args.size[0] / args.size[1]):
        parser.error("--aspect and --size must have matching proportions")
    output = args.output or args.image.with_name(args.image.stem + ".centered.jpg")
    if output.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        parser.error("output must be a JPEG or PNG")
    preview = output.with_name(output.stem + ".detection.png")
    mask_path = output.with_name(output.stem + ".mask.png")
    report = output.with_suffix(".json")
    for target in (output, preview, mask_path, report):
        if target.resolve() == args.image.resolve():
            parser.error("Refusing to overwrite the input image")
        if target.exists() and not args.force:
            parser.error(f"Output already exists: {target}; use --force to replace it")
    try:
        with Image.open(args.image) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGBA")
            base = Image.new("RGBA", oriented.size, args.background)
            base.alpha_composite(oriented)
            image = base.convert("RGB")
        bounds, mask, notes = detect_dragon(
            image, args.threshold, args.seed_threshold, args.close_radius, args.max_side
        )
        crop_box = centered_box(bounds, args.padding, aspect)
        if (
            crop_box[0] < 0
            or crop_box[1] < 0
            or crop_box[2] > image.width
            or crop_box[3] > image.height
        ):
            notes.append(
                "Crop crosses source edges; background padding was added to preserve centering."
            )
        crop = crop_centered(image, crop_box, args.background)
        if args.size:
            crop = crop.resize(tuple(args.size), Image.Resampling.LANCZOS)
        debug = image.copy()
        draw = ImageDraw.Draw(debug)
        line = max(2, round(max(image.size) / 500))
        draw.rectangle(bounds, outline="red", width=line)
        draw.rectangle(crop_box, outline="cyan", width=line)
        cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
        draw.line((cx - 12, cy, cx + 12, cy), fill="red", width=line)
        draw.line((cx, cy - 12, cx, cy + 12), fill="red", width=line)
        output.parent.mkdir(parents=True, exist_ok=True)
        crop.save(
            output, **({"quality": 95, "subsampling": 0} if output.suffix.lower() != ".png" else {})
        )
        debug.save(preview)
        mask.save(mask_path)
        report.write_text(
            json.dumps(
                {
                    "source": str(args.image.resolve()),
                    "source_size": image.size,
                    "bounding_box": bounds,
                    "center": [cx, cy],
                    "crop_box": crop_box,
                    "output_size": crop.size,
                    "threshold": args.threshold,
                    "seed_threshold": args.seed_threshold,
                    "notes": notes,
                    "coordinates": (
                        "EXIF-oriented pixels; left/top inclusive, right/bottom exclusive"
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Dragon candidate: {bounds}; center: ({cx:.1f}, {cy:.1f})")
    print(f"Crop: {output}\nPreview: {preview}\nMask: {mask_path}\nReport: {report}")
    for note in notes:
        print(f"Note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
