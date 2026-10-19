import tempfile
import unittest
from pathlib import Path

from PIL import Image

from birthday_stickers.banners import images_in, main, render_banner


class BannerTests(unittest.TestCase):
    def test_order_geometry_and_print_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, color in [("10.PNG", "blue"), ("2.png", "red")]:
                Image.new("RGB", (80, 80), color).save(root / name)
            paths = images_in(root)
            self.assertEqual([p.name for p in paths], ["2.png", "10.PNG"])
            out = root / "out.png"
            self.assertEqual(render_banner(paths, out, image_size=160, border=20), (200, 380))
            with Image.open(out) as image:
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getpixel((100, 100)), (255, 0, 0))
                self.assertEqual(image.getpixel((100, 280)), (0, 0, 255))
                self.assertAlmostEqual(image.info["dpi"][0], 300, delta=0.1)

    def test_batch_skip_and_bad_folder_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("good", "broken", "empty"):
                (root / "inputs" / name).mkdir(parents=True)
            Image.new("RGB", (40, 60), "red").save(root / "inputs/good/1.png")
            (root / "inputs/broken/1.png").write_text("not an image")
            args = ["--input-dir", str(root / "inputs"), "--output-dir",
                    str(root / "outputs"), "--image-size", "80"]
            self.assertEqual(main(args), 1)
            result = root / "outputs/good.png"
            before = result.read_bytes()
            self.assertEqual(main(args), 1)
            self.assertEqual(result.read_bytes(), before)
            self.assertFalse((root / "outputs/broken.png").exists())

    def test_size_guard(self):
        with self.assertRaisesRegex(ValueError, "reduce --image-size"):
            render_banner([Path("unused.png")] * 100, Path("unused-output.png"))
