import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from birthday_stickers.images import finish_outpaint, plan_padding, save_jpeg


class ImageTests(unittest.TestCase):
    def test_odd_padding_centers_to_within_one_pixel_and_exact_target(self):
        plan = plan_padding((1001, 777), (1500, 1000))
        self.assertEqual(plan.canvas, (1504, 1024))
        self.assertEqual((plan.left, plan.right, plan.top, plan.bottom), (251, 252, 123, 124))
        self.assertEqual(plan.crop_box, (2, 12, 1502, 1012))
        result = finish_outpaint(
            Image.new("RGB", plan.canvas, "blue"),
            Image.new("RGB", plan.source, "red"),
            plan,
            0,
            True,
        )
        self.assertEqual(result.size, (1500, 1000))
        self.assertEqual(result.getpixel((249, 111)), (255, 0, 0))
        self.assertEqual(result.getpixel((248, 111)), (0, 0, 255))

    def test_smaller_target_rejected(self):
        with self.assertRaisesRegex(ValueError, "smaller"):
            plan_padding((1024, 1024), (1000, 1536))

    def test_unexpected_comfy_size_rejected(self):
        plan = plan_padding((32, 32), (64, 64))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            finish_outpaint(Image.new("RGB", (48, 48)), Image.new("RGB", (32, 32)), plan, 4, True)

    def test_feather_blends_boundary_but_preserves_center(self):
        plan = plan_padding((32, 32), (64, 64))
        result = finish_outpaint(
            Image.new("RGB", (64, 64), "blue"), Image.new("RGB", (32, 32), "red"), plan, 4, True
        )
        self.assertEqual(result.getpixel((32, 32)), (255, 0, 0))
        self.assertEqual(result.getpixel((16, 16)), (0, 0, 255))

    def test_jpeg_flattens_transparency_and_is_real_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stiker-0000.jpg"
            source = Image.new("RGBA", (20, 20), (255, 0, 0, 0))
            data = io.BytesIO()
            source.save(data, format="PNG")
            save_jpeg(data.getvalue(), path, 95, 0, "white")
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getpixel((10, 10)), (255, 255, 255))
