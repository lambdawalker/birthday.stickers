import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import test_pipeline as fixtures
from textual.widgets import DataTable, Input, ProgressBar, RichLog, Static, Switch

from birthday_stickers.control import RunHalted
from birthday_stickers.sessions import SessionStore
from birthday_stickers.tui import LogLine, SessionUpdate, StickerApp


class TUITests(unittest.IsolatedAsyncioTestCase):
    async def test_new_pause_resume_stop_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            app = StickerApp(config, store)
            started = threading.Event()

            def fake_run(config, *, resume, control, worker_progress):
                worker_progress("images", 0, "stiker-0000", "rendering_qwen")
                started.set()
                if not control.requested.wait(5):
                    raise RuntimeError("Test control was not requested")
                control.check()
                raise RunHalted("paused")

            with patch("birthday_stickers.sessions.run", fake_run):
                async with app.run_test(size=(110, 40)) as pilot:
                    self.assertTrue(app.query_one("#new-name", Input).value.endswith("-001"))
                    await pilot.click("#choose-duo")
                    await pilot.click("#outpainting")
                    await pilot.click("#new")
                    for _ in range(60):
                        if started.is_set():
                            break
                        await pilot.pause(0.05)
                    self.assertTrue(started.is_set())
                    self.assertFalse(store.list_incomplete()[0]["outpainting"])
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

            def fake_run(config, *, resume, control, worker_progress):
                started.set()
                control.requested.wait(5)
                control.check()

            with patch("birthday_stickers.sessions.run", fake_run):
                async with app.run_test(size=(110, 40)) as pilot:
                    await pilot.click("#choose-duo")
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

    async def test_worker_bars_and_logs_are_independent(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            item = store.create(config, count=5)
            app = StickerApp(config, store)
            async with app.run_test(size=(110, 40)) as pilot:
                app.show_dashboard()
                app.on_session_update(
                    SessionUpdate(
                        {
                            **item,
                            "service_urls": {
                                "prompts": "http://llm:1234/v1",
                                "images": "http://generation:8188",
                                "outpainting": "http://outpaint:8188",
                            },
                            "prompts_completed": 4,
                            "images_completed": 3,
                            "outpainting_completed": 1,
                            "completed": 1,
                            "prompts_stage": "refining",
                            "prompts_sticker": "stiker-0004",
                            "images_stage": "rendering_qwen",
                            "images_sticker": "stiker-0001",
                        }
                    )
                )
                await pilot.pause()
                for lane, url in (
                    ("prompts", "http://llm:1234/v1"),
                    ("images", "http://generation:8188"),
                    ("outpainting", "http://outpaint:8188"),
                ):
                    self.assertEqual(app.query_one(f"#{lane}-url", Static).tooltip, url)
                self.assertEqual(app.query_one("#prompts-progress", ProgressBar).progress, 4)
                self.assertEqual(app.query_one("#images-progress", ProgressBar).progress, 3)
                self.assertEqual(app.query_one("#outpainting-progress", ProgressBar).progress, 1)
                app.on_log_line(LogLine("Flux finished in 30.00s", "outpainting"))
                app.on_log_line(LogLine("Plan scene finished in 1.25s", "prompts"))
                app.on_log_line(LogLine("Qwen render finished in 20.50s", "images"))
                await pilot.pause()
                self.assertIn("Plan scene", str(app.query_one("#prompts-logs", RichLog).lines))
                self.assertNotIn("Qwen render", str(app.query_one("#prompts-logs", RichLog).lines))
                self.assertIn("Qwen render", str(app.query_one("#images-logs", RichLog).lines))
                self.assertIn(
                    "Flux finished", str(app.query_one("#outpainting-logs", RichLog).lines)
                )
                self.assertNotIn("Flux finished", str(app.query_one("#images-logs", RichLog).lines))

    async def test_invalid_count_and_start_error_are_visible(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            app = StickerApp(config, SessionStore(Path(temp) / "sessions"))
            async with app.run_test(size=(110, 40)) as pilot:
                await pilot.click("#choose-duo")
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

    async def test_type_picker_switches_profiles_and_keeps_connection_settings(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            from birthday_stickers.config import load_config

            config = fixtures.PipelineTests().configuration(temp, service)
            dragon = load_config(fixtures.ROOT / "config.dragon.yaml")
            app = StickerApp(config, SessionStore(Path(temp) / "sessions"))
            async with app.run_test(size=(80, 24)) as pilot:
                self.assertTrue(app.query_one("#type-picker").display)
                self.assertFalse(app.query_one("#home").display)
                with patch("birthday_stickers.tui.load_config", return_value=dragon):
                    await pilot.click("#choose-dragon")
                self.assertEqual(app.config.generation.type, "single_character_text")
                self.assertFalse(app.query_one("#outpainting", Switch).value)
                await pilot.click("#outpainting")
                self.assertTrue(app.query_one("#outpainting", Switch).value)
                self.assertEqual(app.config.llm.base_url, config.llm.base_url)
                self.assertEqual(app.config.comfy.base_url, config.comfy.base_url)
                self.assertEqual(app.config.output.directory, config.output.directory)
                self.assertTrue(app.query_one("#home").display)
                await pilot.click("#change-type")
                self.assertTrue(app.query_one("#type-picker").display)
                await pilot.click("#choose-duo")
                self.assertEqual(app.config.generation.type, "multi_character_reference")
                self.assertEqual(app.config.protagonist, config.protagonist)

    async def test_missing_profile_keeps_picker_open_with_error(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            app = StickerApp(config)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.click("#choose-dragon")
                self.assertTrue(app.query_one("#type-picker").display)
                self.assertIn("Unable to load", str(app.query_one("#type-error", Static).render()))
                await pilot.click("#choose-duo")
                self.assertTrue(app.query_one("#home").display)

    async def test_saved_session_toggle_tracks_selection_and_applies_on_resume(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            first = store.create(config)
            config.generation.outpainting = False
            second = store.create(config)
            app = StickerApp(config, store)
            async with app.run_test(size=(110, 45)) as pilot:
                await pilot.click("#choose-duo")
                await pilot.pause()
                table = app.query_one("#sessions", DataTable)
                switch = app.query_one("#session-outpainting", Switch)
                self.assertFalse(switch.value)
                table.move_cursor(row=1)
                await pilot.pause()
                self.assertTrue(switch.value)
                table.focus()
                with patch.object(app, "begin") as begin:
                    await pilot.press("enter")
                    begin.assert_not_called()
                await pilot.click("#session-outpainting")
                self.assertFalse(switch.value)
                with patch.object(app, "begin") as begin:
                    await pilot.click("#open")
                    begin.assert_called_once_with(first["name"])
                self.assertFalse(store.read(first["name"])["outpainting"])
                table.move_cursor(row=0)
                await pilot.pause()
                self.assertFalse(switch.value)
                await pilot.click("#session-outpainting")
                with patch.object(app, "begin") as begin:
                    await pilot.click("#open")
                    begin.assert_called_once_with(second["name"])
                self.assertTrue(store.read(second["name"])["outpainting"])
