import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from birthday_stickers.gallery import Gallery, main
from birthday_stickers.wifi_watchdog import wifi_watchdog


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "flat"
        self.source.mkdir()
        self.image = self.source / "old-name.jpg"
        Image.new("RGB", (20, 20), "green").save(self.image)
        self.gallery = Gallery(self.root / "gallery")

    def test_repeat_flat_import_preserves_originals(self):
        args = [str(self.source), "--gallery", str(self.gallery.root)]
        self.assertEqual(main(args), 0)
        digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.assertEqual(
            (self.gallery.root / "image_generation" / (digest + ".jpg")).read_bytes(),
            self.image.read_bytes(),
        )
        self.assertEqual(main(args), 0)
        self.assertEqual(len(list((self.gallery.root / "image_generation").glob("*.jpg"))), 1)
        self.assertTrue(self.image.exists())

    def test_export_pipeline_isolation_and_concurrent_deduplication(self):
        sessions = self.root / "sessions"
        for name in ("one", "two"):
            for pipeline in ("image_generation", "outpainting"):
                folder = sessions / name / pipeline / "results"
                folder.mkdir(parents=True)
                (folder / "stiker-0000.jpg").write_bytes(self.image.read_bytes())
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.gallery.export_sessions(sessions), range(2)))
        self.assertEqual(sum(r["image_generation"]["added"] for r in results), 1)
        self.assertEqual(sum(r["outpainting"]["added"] for r in results), 1)
        with self.gallery.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 4)

    def test_bad_image_does_not_interrupt_import(self):
        bad = self.source / "bad.jpg"
        bad.write_text("not an image", encoding="utf-8")
        result = self.gallery.import_files([bad, self.image], "image_generation")
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertFalse(list(self.gallery.root.glob("*/.import-*")))

    def test_wifi_process_is_reaped_on_error(self):
        child = MagicMock()
        child.poll.return_value = None
        with (
            patch("birthday_stickers.wifi_watchdog.sys.platform", "win32"),
            patch("birthday_stickers.wifi_watchdog.subprocess.Popen", return_value=child) as launch,
        ):
            with self.assertRaisesRegex(RuntimeError, "test"):
                with wifi_watchdog(self.root / "logs"):
                    raise RuntimeError("test")
        self.assertIn("--background", launch.call_args.args[0])
        child.terminate.assert_called_once()
        child.wait.assert_called_once()


class GalleryUITests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_gallery_button_is_repeatable(self):
        import test_pipeline as fixtures

        from birthday_stickers.sessions import SessionStore
        from birthday_stickers.tui import StickerApp

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            root = Path(temp)
            sessions = root / "sessions"
            source = sessions / "example/image_generation/results"
            source.mkdir(parents=True)
            Image.new("RGB", (16, 16), "blue").save(source / "stiker-0000.jpg")
            app = StickerApp(config, SessionStore(sessions))
            async with app.run_test(size=(120, 60)) as pilot:
                await pilot.click("#choose-preset-1")
                await pilot.click("#generate-gallery")
                await app.workers.wait_for_complete()
                await pilot.click("#generate-gallery")
                await app.workers.wait_for_complete()
                self.assertEqual(len(list((root / "gallery/image_generation").glob("*.jpg"))), 1)
                self.assertTrue((root / "gallery/outpainting").is_dir())
                self.assertTrue((source / "stiker-0000.jpg").is_file())
