import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from PIL import Image

from birthday_stickers.character_conversion import convert_characters
from birthday_stickers.control import RunControl, RunHalted
from birthday_stickers.http import HTTPClient
from birthday_stickers.retry import file_retry, pause, retry_scope


class RetryTests(unittest.TestCase):
    def test_file_locks_retry_and_other_errors_fail(self):
        calls = []

        @file_retry
        def operation():
            calls.append(1)
            if len(calls) < 19:
                raise PermissionError(errno.EACCES, "locked")
            return 42

        with patch("birthday_stickers.retry.pause") as wait:
            self.assertEqual(operation(), 42)
            self.assertEqual([call.args[0] for call in wait.call_args_list], list(range(1, 19)))
        with self.assertRaises(FileNotFoundError):
            file_retry(lambda: Path("/missing-reference-123").read_bytes())()

    def test_delay_caps_and_pause_is_interruptible(self):
        for attempt, delay in ((1, 1), (2, 2), (15, 15), (25, 15)):
            with (
                patch("birthday_stickers.retry.time.monotonic", side_effect=[0, delay + 1]),
                patch("birthday_stickers.retry.log.warning") as log,
            ):
                pause(attempt, "test", "offline")
                self.assertEqual(log.call_args.args[-1], delay)
        control = RunControl()
        control.request()
        with retry_scope(control.check), self.assertRaises(RunHalted):
            pause(99, "test", "offline")

    def test_network_retries_more_than_old_limit(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with (
            patch(
                "birthday_stickers.http.urlopen",
                side_effect=[URLError("offline")] * 20 + [response],
            ),
            patch("birthday_stickers.http.pause") as wait,
        ):
            self.assertEqual(HTTPClient("http://local", 1).json("GET", "/history"), {})
            self.assertEqual(wait.call_count, 20)

    def test_character_conversion_collision_and_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (16, 16), "red").save(root / "A789.jpg")
            original = (root / "A789.jpg").read_bytes()
            Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(root / "A789.png")
            Image.new("RGB", (16, 16), "blue").save(root / "B12.png")
            convert_characters(root)
            self.assertEqual((root / "A789.jpg").read_bytes(), original)
            self.assertTrue((root / "A790.jpg").exists())
            self.assertTrue((root / "B12.jpg").exists())
            self.assertTrue((root / "A789.png").exists())
            with Image.open(root / "A790.jpg") as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            convert_characters(root)
            self.assertEqual(len(list(root.glob("*.jpg"))), 3)

    def test_locked_image_write_recovers_without_changing_contents(self):
        import os

        from birthday_stickers.images import save_jpeg

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "image.jpg"
            replace = os.replace
            attempts = []

            def locked_replace(source, dest):
                attempts.append(1)
                if len(attempts) < 3:
                    raise PermissionError(errno.EACCES, "viewer has file locked")
                return replace(source, dest)

            with (
                patch("birthday_stickers.images.os.replace", side_effect=locked_replace),
                patch("birthday_stickers.retry.pause"),
            ):
                save_jpeg(Image.new("RGB", (20, 20), "red"), target, 95, 0, "white")
            with Image.open(target) as image:
                self.assertEqual(image.size, (20, 20))
            self.assertEqual(len(attempts), 3)

    def test_lost_submission_can_resume_without_duplicate_job(self):
        import test_pipeline as fixtures

        from birthday_stickers.comfy import ComfyClient
        from birthday_stickers.pipeline import run

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            service.fail_submit = True
            with patch.object(ComfyClient, "recover_submission", side_effect=RunHalted("paused")):
                with self.assertRaises(RunHalted):
                    run(config)
            self.assertEqual(len(service.graphs), 1)
            service.fail_submit = False
            run(config, resume=True)
            self.assertEqual(len(service.graphs), 2)  # Original Qwen plus one outpaint.

    def test_pause_while_server_refuses_connection_marks_submission_unsent(self):
        control = RunControl()
        control.request()
        with (
            patch("birthday_stickers.http.urlopen", side_effect=URLError(ConnectionRefusedError())),
            retry_scope(control.check),
        ):
            with self.assertRaises(RunHalted) as caught:
                HTTPClient("http://local", 1).json("POST", "/prompt", {})
            self.assertTrue(caught.exception.submission_not_sent)

    def test_standalone_converter_preserves_existing_character_jpg(self):
        import runpy

        converter = runpy.run_path(str(Path(__file__).resolve().parents[1] / "convert_to_jpg.py"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (16, 16), "red").save(root / "A789.jpg")
            before = (root / "A789.jpg").read_bytes()
            Image.new("RGB", (16, 16), "blue").save(root / "A789.png")
            self.assertTrue(converter["convert_image_to_jpg"](root / "A789.png"))
            self.assertEqual((root / "A789.jpg").read_bytes(), before)
            self.assertTrue((root / "A790.jpg").exists())
