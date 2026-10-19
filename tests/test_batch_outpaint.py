import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import test_pipeline as fixtures
import yaml
from PIL import Image
from textual.widgets import ContentSwitcher, ListView

from birthday_stickers.batch_outpaint import run_batch, settings
from birthday_stickers.comfy import ComfyClient
from birthday_stickers.control import RunControl, RunHalted
from birthday_stickers.llm import LLMClient
from birthday_stickers.outpaint_tui import OutpaintApp
from birthday_stickers.tui import StickerApp


class BatchTests(unittest.TestCase):
    def config(self, root, service):
        source = root / "source"
        source.mkdir()
        Image.new("RGB", (32, 32), "red").save(source / "one.png")
        config = yaml.safe_load((fixtures.ROOT / "outpaint.yaml").read_text(encoding="utf-8"))
        config.update(source=str(source), destination=str(root / "output"), width=64, height=64)
        config["llm"]["base_url"] = service.url + "/v1"
        config["comfy"].update(base_url=service.url, poll_interval=0.002)
        path = root / "batch.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return settings(path)

    def test_vision_description_render_and_repeat_without_duplicate_jobs(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            result = run_batch(config)
            self.assertEqual((result["rendered"], result["failed"]), (1, 0))
            self.assertEqual(len(service.chats), 1)
            self.assertEqual(len(service.graphs), 1)
            content = service.chats[0]["messages"][1]["content"]
            self.assertEqual(content[1]["type"], "image_url")
            self.assertIn("207", service.graphs[0])
            output = next((Path(config["destination"]) / "results").glob("*.jpg"))
            self.assertEqual(output.name, "one.jpg")
            with Image.open(output) as image:
                self.assertEqual(image.size, (64, 64))
            run_batch(config)
            self.assertEqual(len(service.chats), 1)
            self.assertEqual(len(service.graphs), 1)

    def test_next_description_runs_while_first_image_is_rendering(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            Image.new("RGB", (32, 32), "blue").save(Path(config["source"]) / "two.png")
            rendering = Event()
            second_description = Event()
            complete = LLMClient.complete
            wait_image = ComfyClient.wait_image
            calls = 0

            def describe(client, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self.assertTrue(rendering.wait(5), "Rendering did not overlap descriptions")
                result = complete(client, *args, **kwargs)
                if calls == 2:
                    second_description.set()
                return result

            def render(client, *args, **kwargs):
                rendering.set()
                self.assertTrue(second_description.wait(5), "LLM waited for rendering to finish")
                return wait_image(client, *args, **kwargs)

            with (
                patch.object(LLMClient, "complete", describe),
                patch.object(ComfyClient, "wait_image", render),
            ):
                result = run_batch(config)
            self.assertEqual(result, {"described": 2, "rendered": 2, "failed": 0, "total": 2})

    def test_completed_older_run_survives_rename_and_settings_changes(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            run_batch(config)
            state = next(
                p
                for p in (Path(config["destination"]) / "state").glob("*.json")
                if not p.name.endswith(".workflow.json")
            )
            record = json.loads(state.read_text(encoding="utf-8"))
            record.pop("image_identity", None)
            state.write_text(json.dumps(record), encoding="utf-8")
            source = Path(config["source"])
            (source / "one.png").rename(source / "renamed.png")
            config["cfg"] = 2.0
            self.assertEqual(run_batch(config)["rendered"], 1)
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(len(service.chats), 1)
            for output in (Path(config["destination"]) / "results").glob("*.jpg"):
                output.unlink()
            self.assertEqual(run_batch(config)["rendered"], 1)
            self.assertEqual(len(service.graphs), 2)

    def test_pause_after_description_reuses_saved_prompt(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            control = RunControl()

            def report(kind, value):
                if kind == "progress" and value["described"]:
                    control.request()

            with self.assertRaises(RunHalted):
                run_batch(config, control, report)
            self.assertEqual(len(service.graphs), 0)
            self.assertEqual(run_batch(config)["rendered"], 1)
            self.assertEqual(len(service.chats), 1)

    def test_resume_submitted_job_does_not_submit_again(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            with patch.object(ComfyClient, "wait_image", side_effect=RunHalted("paused")):
                with self.assertRaises(RunHalted):
                    run_batch(config)
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(run_batch(config)["rendered"], 1)
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(len(service.chats), 1)


class BatchUITests(unittest.IsolatedAsyncioTestCase):
    async def test_quit_shortcuts(self):
        config = settings(fixtures.ROOT / "outpaint.yaml")
        app = OutpaintApp(config)
        async with app.run_test() as pilot:
            app.query_one("#start").focus()
            with patch.object(app, "exit") as leave:
                await pilot.press("q")
                leave.assert_called_once()
            app.query_one("#source").focus()
            with patch.object(app, "exit") as leave:
                await pilot.press("ctrl+q")
                leave.assert_called_once()

    async def test_controls_and_list_picker(self):
        config = settings(fixtures.ROOT / "outpaint.yaml")
        app = OutpaintApp(config)
        with patch(
            "birthday_stickers.outpaint_tui.run_batch", return_value={"rendered": 0, "failed": 0}
        ):
            async with app.run_test(size=(100, 42)) as pilot:
                self.assertEqual(app.query_one("#pages", ContentSwitcher).current, "setup")
                await pilot.click("#start")
                await app.workers.wait_for_complete()
                self.assertFalse(app.busy)
                self.assertEqual(app.query_one("#pages", ContentSwitcher).current, "monitor")
                await pilot.click("#resume")
                await app.workers.wait_for_complete()
                await pilot.click("#back")
                self.assertEqual(app.query_one("#pages", ContentSwitcher).current, "setup")
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            original = fixtures.PipelineTests().configuration(temp, service)
            app = StickerApp(original)
            async with app.run_test(size=(120, 50)) as pilot:
                self.assertIsInstance(app.query_one("#preset-list"), ListView)
                await pilot.click("#choose-preset-1")
                self.assertFalse(app.query_one("#type-picker").display)
