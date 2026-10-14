import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import test_pipeline as fixtures
from textual.widgets import DataTable, Input, Static

from birthday_stickers.control import RunHalted
from birthday_stickers.sessions import SessionStore
from birthday_stickers.tui import StickerApp


class TUITests(unittest.IsolatedAsyncioTestCase):
    async def test_new_pause_resume_stop_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            app = StickerApp(config, store)
            started = threading.Event()

            def fake_run(config, *, resume, control, progress):
                progress(0, "stiker-0000", "rendering_qwen")
                started.set()
                if not control.requested.wait(5):
                    raise RuntimeError("Test control was not requested")
                control.check()
                raise RunHalted("paused")

            with patch("birthday_stickers.sessions.run", fake_run):
                async with app.run_test(size=(110, 40)) as pilot:
                    self.assertTrue(app.query_one("#new-name", Input).value.endswith("-001"))
                    await pilot.click("#new")
                    for _ in range(60):
                        if started.is_set():
                            break
                        await pilot.pause(0.05)
                    self.assertTrue(started.is_set())
                    await pilot.click("#pause")
                    for _ in range(60):
                        if not app.busy:
                            break
                        await pilot.pause(0.05)
                    self.assertFalse(app.busy)
                    self.assertEqual(app.active["status"], "paused")
                    started.clear()
                    await pilot.click("#resume")
                    for _ in range(60):
                        if started.is_set():
                            break
                        await pilot.pause(0.05)
                    self.assertTrue(started.is_set())
                    await pilot.click("#stop")
                    for _ in range(60):
                        if not app.busy:
                            break
                        await pilot.pause(0.05)
                    self.assertTrue(app.query_one("#home").display)
                    self.assertEqual(app.query_one("#sessions", DataTable).row_count, 1)
                    self.assertEqual(store.list_incomplete()[0]["status"], "stopped")
            reopened = StickerApp(config, SessionStore(store.root))
            async with reopened.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                self.assertEqual(reopened.query_one("#sessions", DataTable).row_count, 1)

    async def test_ctrl_q_requests_stop_before_exiting_worker(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            app = StickerApp(config, store)
            started = threading.Event()

            def fake_run(config, *, resume, control, progress):
                started.set()
                control.requested.wait(5)
                control.check()

            with patch("birthday_stickers.sessions.run", fake_run):
                async with app.run_test(size=(110, 40)) as pilot:
                    await pilot.click("#new")
                    for _ in range(60):
                        if started.is_set():
                            break
                        await pilot.pause(0.05)
                    self.assertTrue(started.is_set())
                    await pilot.press("ctrl+q")
                    for _ in range(60):
                        if not app.busy:
                            break
                        await pilot.pause(0.05)
                    self.assertTrue(app.control.requested.is_set())
                    self.assertFalse(app.busy)
                    self.assertEqual(store.list_incomplete()[0]["status"], "stopped")

    async def test_invalid_count_and_start_error_are_visible(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            app = StickerApp(config, SessionStore(Path(temp) / "sessions"))
            async with app.run_test(size=(110, 40)) as pilot:
                app.query_one("#count", Input).value = "0"
                await pilot.click("#new")
                self.assertFalse(app.busy)
                await pilot.pause(0.4)
                app.query_one("#count", Input).value = "1"
                with patch.object(
                    app.store, "create", side_effect=ValueError("Missing references")
                ):
                    await pilot.click("#new")
                    for _ in range(60):
                        if "Missing references" in str(app.query_one("#error", Static).render()):
                            break
                        await pilot.pause(0.05)
                self.assertIn("Missing references", str(app.query_one("#error", Static).render()))
