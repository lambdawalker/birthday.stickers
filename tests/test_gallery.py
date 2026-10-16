import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request, urlopen

from PIL import Image

from birthday_stickers.gallery import Gallery, main
from birthday_stickers.review.server import make_server
from birthday_stickers.review.store import ReviewStore
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

    def test_repeat_flat_import_preserves_originals_and_reviews(self):
        args = [str(self.source), "--gallery", str(self.gallery.root)]
        self.assertEqual(main(args), 0)
        digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.assertEqual(
            (self.gallery.root / "image_generation" / (digest + ".jpg")).read_bytes(),
            self.image.read_bytes(),
        )
        store = ReviewStore(self.gallery.root)
        store.decide("maria", "image_generation", digest, "favorite", "a" * 32)
        self.assertEqual(main(args), 0)
        self.assertEqual(len(list((self.gallery.root / "image_generation").glob("*.jpg"))), 1)
        self.assertEqual(
            len(store.collection("maria", "image_generation", "favorites")["images"]), 1
        )
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
        bad.write_text("not an image")
        result = self.gallery.import_files([bad, self.image], "image_generation")
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertFalse(list(self.gallery.root.glob("*/.import-*")))

    def test_reviews_are_independent_persistent_and_undo_is_idempotent(self):
        self.gallery.add(self.image)
        store = ReviewStore(self.gallery.root)
        key = store.collection("maria", "image_generation")["images"][0]["hash"]

        def act(action, token):
            store.decide("maria", "image_generation", key, action, token * 32)

        act("favorite", "a")
        act("favorite", "a")  # response lost: repeat must not toggle off
        act("tshirt", "b")
        self.assertEqual(len(store.collection("juan", "image_generation")["images"]), 1)
        act("reject", "c")
        self.assertEqual(
            len(store.collection("maria", "image_generation", "favorites")["images"]), 0
        )
        act("undo", "d")
        act("undo", "d")
        reopened = ReviewStore(self.gallery.root)
        row = reopened.collection("maria", "image_generation", "accepted")["images"][0]
        self.assertEqual((row["favorite"], row["tshirt"]), (1, 1))
        act("undo", "e")
        act("undo", "f")
        self.assertEqual(len(reopened.collection("maria", "image_generation")["images"]), 1)

    def test_reset_clears_only_selected_session_and_cannot_be_undone(self):
        self.gallery.add(self.image)
        self.gallery.import_files([self.image], "outpainting")
        store = ReviewStore(self.gallery.root)
        key = store.collection("maria", "image_generation")["images"][0]["hash"]
        store.decide("maria", "image_generation", key, "favorite", "a" * 32)
        store.decide("juan", "image_generation", key, "accept", "b" * 32)
        store.decide("maria", "outpainting", key, "tshirt", "c" * 32)
        store.decide("maria", "image_generation", None, "reset", "r" * 32)
        store.decide("maria", "image_generation", None, "undo", "u" * 32)
        self.assertEqual(len(store.collection("maria", "image_generation")["images"]), 1)
        self.assertEqual(len(store.collection("juan", "image_generation", "accepted")["images"]), 1)
        self.assertEqual(len(store.collection("maria", "outpainting", "tshirt")["images"]), 1)
        store.decide("maria", "image_generation", key, "accept", "d" * 32)
        store.decide("maria", "image_generation", None, "reset", "r" * 32)
        self.assertEqual(
            len(store.collection("maria", "image_generation", "accepted")["images"]), 1
        )
        self.assertTrue(self.image.exists())

    def test_http_gallery_decision_and_sessions(self):
        self.gallery.add(self.image)
        server = make_server(self.gallery.root, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/user/maria") as response:
                self.assertIn(b'id="undo"', response.read())
            with urlopen(base + "/api/user/maria") as response:
                item = json.load(response)["images"][0]
            body = json.dumps(
                {"hash": item["hash"], "action": "favorite", "token": "f" * 32}
            ).encode()
            with urlopen(
                Request(
                    base + "/api/user/maria",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
            ) as response:
                self.assertTrue(json.load(response)["ok"])
            with urlopen(base + "/api/sessions") as response:
                self.assertEqual(json.load(response)[0]["favorites"], 1)
            with urlopen(base + "/images/image_generation/" + item["filename"]) as response:
                self.assertEqual(response.read(), self.image.read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

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
