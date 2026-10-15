import runpy
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/center_dragon.py"
API = runpy.run_path(str(SCRIPT))


class CenterDragonTests(unittest.TestCase):
    def test_dark_subject_with_highlight_and_unrelated_frame(self):
        image = Image.new("RGB", (240, 180), "#e8cfda")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 239, 179), outline="black", width=4)
        draw.rectangle((80, 55, 159, 124), fill="#222222")
        draw.rectangle((160, 70, 179, 100), fill="#888888")
        draw.rectangle((100, 75, 110, 85), fill="white")
        box, mask, _ = API["detect_dragon"](image, close_radius=0)
        self.assertEqual(box, (80, 55, 180, 125))
        self.assertEqual(mask.getpixel((1, 1)), 0)
        self.assertEqual(mask.getpixel((170, 80)), 255)
        crop = API["centered_box"](box, padding=0.15, aspect=1)
        self.assertLessEqual(abs((crop[0] + crop[2]) / 2 - 130), 0.5)
        self.assertLessEqual(abs((crop[1] + crop[3]) / 2 - 90), 0.5)
        self.assertEqual(crop[2] - crop[0], crop[3] - crop[1])

    def test_edge_crop_pads_instead_of_moving_subject_off_center(self):
        image = Image.new("RGB", (100, 100), "pink")
        ImageDraw.Draw(image).rectangle((0, 40, 19, 59), fill="black")
        box, _, notes = API["detect_dragon"](image, close_radius=0)
        crop_box = API["centered_box"](box, padding=0.5)
        result = API["crop_centered"](image, crop_box)
        self.assertEqual(result.size, (40, 40))
        self.assertEqual(result.getpixel((0, 20)), (255, 255, 255))
        self.assertEqual(result.getpixel((20, 20)), (0, 0, 0))
        self.assertTrue(notes)

    def test_edge_subject_is_not_rejected_in_favor_of_detached_wing(self):
        image = Image.new("RGB", (240, 180), "pink")
        draw = ImageDraw.Draw(image)
        draw.rectangle((160, 40, 239, 120), fill="black")
        draw.rectangle((145, 70, 158, 85), fill="black")
        box, _, notes = API["detect_dragon"](image, close_radius=0)
        self.assertEqual(box, (145, 40, 240, 121))
        self.assertTrue(notes)

    def test_no_dark_subject_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, "No substantial dark subject"):
            API["detect_dragon"](Image.new("RGB", (100, 100), "pink"))

    def test_cli_outputs_preview_mask_report_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.png"
            image = Image.new("RGB", (100, 100), "pink")
            ImageDraw.Draw(image).ellipse((30, 20, 80, 75), fill="black")
            image.save(source)
            original = source.read_bytes()
            self.assertEqual(API["main"]([str(source), "--size", "160", "160"]), 0)
            for suffix in ("jpg", "detection.png", "mask.png", "json"):
                self.assertTrue((Path(temp) / ("source.centered." + suffix)).is_file())
            with Image.open(Path(temp) / "source.centered.jpg") as result:
                self.assertEqual(result.size, (160, 160))
            self.assertEqual(source.read_bytes(), original)
            with self.assertRaises(SystemExit):
                API["main"]([str(source), "--output", str(source), "--force"])

    def test_directory_recursion_collisions_skip_and_failure_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = Image.new("RGB", (100, 100), "pink")
            ImageDraw.Draw(image).ellipse((30, 20, 80, 75), fill="black")
            image.save(root / "scene.jpg")
            image.save(root / "scene.png")
            image.save(root / "old.centered.jpg")
            (root / "nested").mkdir()
            image.save(root / "nested" / "other.png")
            self.assertEqual(API["main"]([temp]), 0)
            output = root / "centered"
            self.assertTrue((output / "scene.jpg.centered.jpg").exists())
            self.assertTrue((output / "scene.png.centered.jpg").exists())
            self.assertFalse((output / "old.centered.jpg.centered.jpg").exists())
            before = (output / "scene.jpg.centered.jpg").stat().st_mtime_ns
            self.assertEqual(API["main"]([temp, "--recursive"]), 0)
            self.assertTrue((output / "nested" / "other.png.centered.jpg").exists())
            self.assertFalse((output / "centered").exists())
            self.assertEqual((output / "scene.jpg.centered.jpg").stat().st_mtime_ns, before)
            (root / "broken.jpg").write_bytes(b"not an image")
            image.save(root / "valid.jpg")
            self.assertEqual(API["main"]([temp]), 1)
            self.assertTrue((output / "valid.jpg.centered.jpg").exists())
