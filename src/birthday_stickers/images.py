"""Image dimensions, centered outpainting, and compact JPEG storage."""

import io
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageOps


@dataclass(frozen=True)
class PaddingPlan:
    source: tuple[int, int]
    target: tuple[int, int]
    canvas: tuple[int, int]
    left: int
    top: int
    right: int
    bottom: int
    crop_box: tuple[int, int, int, int]


def plan_padding(source: tuple[int, int], target: tuple[int, int]) -> PaddingPlan:
    if min(*source, *target) <= 0:
        raise ValueError("Image dimensions must be positive")
    if target[0] < source[0] or target[1] < source[1]:
        raise ValueError(
            f"Target {target} is smaller than source {source}; outpainting cannot shrink it"
        )
    canvas = tuple((value + 15) // 16 * 16 for value in target)
    dx, dy = canvas[0] - source[0], canvas[1] - source[1]
    left, top = dx // 2, dy // 2
    crop_x = left - (target[0] - source[0]) // 2
    crop_y = top - (target[1] - source[1]) // 2
    return PaddingPlan(
        source,
        target,
        canvas,
        left,
        top,
        dx - left,
        dy - top,
        (crop_x, crop_y, crop_x + target[0], crop_y + target[1]),
    )


def decode_image(data: bytes | Path | Image.Image) -> Image.Image:
    if isinstance(data, Image.Image):
        return ImageOps.exif_transpose(data).copy()
    with Image.open(io.BytesIO(data) if isinstance(data, bytes) else data) as image:
        return ImageOps.exif_transpose(image).copy()


def rgb_image(image: Image.Image, background: str = "white") -> Image.Image:
    rgba = image.convert("RGBA")
    result = Image.new("RGB", rgba.size, ImageColor.getrgb(background))
    result.paste(rgba, mask=rgba.getchannel("A"))
    return result


def save_jpeg(
    data: bytes | Path | Image.Image, path: Path, quality: int, subsampling: int, background: str
) -> None:
    image = rgb_image(decode_image(data), background)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jpg.tmp")
    try:
        image.save(
            temporary,
            format="JPEG",
            quality=quality,
            subsampling=subsampling,
            optimize=True,
            progressive=True,
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def finish_outpaint(
    generated: Image.Image,
    source: Image.Image,
    plan: PaddingPlan,
    feathering: int,
    preserve_center: bool,
) -> Image.Image:
    if generated.size != plan.canvas or source.size != plan.source:
        raise ValueError(
            f"Unexpected ComfyUI dimensions: generated {generated.size}, expected {plan.canvas}; "
            f"source {source.size}, expected {plan.source}"
        )
    result = generated.convert("RGB")
    if preserve_center:
        # Only feather edges with added space. The interior stays anchored to the
        # intermediate image, protecting the characters against VAE/diffusion drift.
        width, height = source.size
        mask = Image.new("L", source.size, 255)
        draw = ImageDraw.Draw(mask)
        feather = min(feathering, (width - 1) // 2, (height - 1) // 2)
        for distance in range(feather):
            value = round(255 * distance / feather)
            if plan.left:
                draw.line((distance, 0, distance, height - 1), fill=value)
            if plan.right:
                draw.line((width - 1 - distance, 0, width - 1 - distance, height - 1), fill=value)
        # Use minimum at corners instead of overwriting the horizontal fade.
        if plan.top or plan.bottom:
            from PIL import ImageChops

            vertical = Image.new("L", source.size, 255)
            draw = ImageDraw.Draw(vertical)
            for distance in range(feather):
                value = round(255 * distance / feather)
                if plan.top:
                    draw.line((0, distance, width - 1, distance), fill=value)
                if plan.bottom:
                    draw.line(
                        (0, height - 1 - distance, width - 1, height - 1 - distance), fill=value
                    )
            mask = ImageChops.darker(mask, vertical)
        result.paste(source.convert("RGB"), (plan.left, plan.top), mask)
    return result.crop(plan.crop_box)
