import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import test_pipeline as fixtures
import yaml
from PIL import Image
from textual.widgets import ContentSwitcher, Input

from birthday_stickers.batch_repair import decision, run_batch, settings
from birthday_stickers.comfy import ComfyClient
from birthday_stickers.control import RunControl, RunHalted
from birthday_stickers.llm import LLMClient
from birthday_stickers.outpaint_tui import OutpaintApp

YES = '{"answer": true, "reason": "Visible in the scene"}'
NO = '{"answer": false, "reason": "Not visible"}'
EDIT = "Fix the lenses: black semitransparent glass reflecting the nearby green trees and blue sky."


class RepairTests(unittest.TestCase):
    def config(self, root, service, count=1):
        source = root / "source"
        source.mkdir()
        for i in range(count):
            Image.new("RGB", (32, 32), (i * 20, 50, 80)).save(source / f"image-{i}.png")
        config = yaml.safe_load((fixtures.ROOT / "glasses-repair.yaml").read_text(encoding="utf-8"))
        config.update(source=str(source), destination=str(root / "output"))
        config["llm"]["base_url"] = service.url + "/v1"
        config["comfy"].update(base_url=service.url, poll_interval=0.002)
        path = root / "repair.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return settings(path)

    def test_filter_stages_and_edit_only_flagged_images(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service, 3)
            with patch.object(
                LLMClient, "complete", side_effect=[NO, YES, NO, YES, YES, EDIT]
            ) as llm:
                result = run_batch(config)
            self.assertEqual(result["skipped"], 2)
            self.assertEqual(result["rendered"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(llm.call_count, 6)
            self.assertEqual(len(service.graphs), 1)
            graph = service.graphs[0]
            self.assertEqual(graph["459:474"]["inputs"]["prompt"], EDIT)
            self.assertEqual(graph["459:474"]["inputs"]["images.image_1"], ["image", 0])
            self.assertFalse(any(n["class_type"] == "ImagePadForOutpaint" for n in graph.values()))
            self.assertEqual(
                [p.name for p in (Path(config["destination"]) / "results").iterdir()],
                ["image-2.jpg"],
            )
            with patch.object(LLMClient, "complete", side_effect=AssertionError("Unexpected LLM")):
                rerun = run_batch(config)
            self.assertEqual(rerun["failed"], 0)
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(len(list(Path(config["source"]).glob("*.png"))), 3)

    def test_invalid_or_uncertain_is_not_treated_as_no(self):
        for value in ('{"answer": null, "reason": "unclear"}', "not json", '{"answer": "false"}'):
            with self.assertRaises(ValueError):
                decision(value)
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            with patch.object(LLMClient, "complete", return_value='{"answer": null}'):
                self.assertEqual(run_batch(config)["failed"], 1)
            self.assertEqual(len(service.graphs), 0)
            with patch.object(LLMClient, "complete", side_effect=[YES, YES, EDIT]):
                self.assertEqual(run_batch(config)["rendered"], 1)

    def test_pause_and_resume_submitted_job_and_deleted_result(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            with (
                patch.object(LLMClient, "complete", side_effect=[YES, YES, EDIT]),
                patch.object(ComfyClient, "wait_image", side_effect=RunHalted("paused")),
            ):
                with self.assertRaises(RunHalted):
                    run_batch(config)
            self.assertEqual(len(service.graphs), 1)
            with patch.object(LLMClient, "complete", side_effect=AssertionError("Unexpected LLM")):
                self.assertEqual(run_batch(config)["rendered"], 1)
            self.assertEqual(len(service.graphs), 1)
            next((Path(config["destination"]) / "results").glob("*.jpg")).unlink()
            self.assertEqual(run_batch(config)["rendered"], 1)
            self.assertEqual(len(service.graphs), 2)
            self.assertNotEqual(
                service.graphs[0]["459:458"]["inputs"]["seed"],
                service.graphs[1]["459:458"]["inputs"]["seed"],
            )

    def test_pause_after_first_gate_saves_it(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service)
            control = RunControl()

            def report(kind, value):
                if kind == "prompts_log" and value.startswith("girl: True"):
                    control.request()

            with patch.object(LLMClient, "complete", return_value=YES):
                with self.assertRaises(RunHalted):
                    run_batch(config, control, report)
            with patch.object(LLMClient, "complete", side_effect=[YES, EDIT]) as llm:
                self.assertEqual(run_batch(config)["rendered"], 1)
            self.assertEqual(llm.call_count, 2)

    def test_analysis_overlaps_rendering(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(Path(temp), service, 2)
            rendering, next_prompt = Event(), Event()
            wait_image = ComfyClient.wait_image
            calls = 0

            def complete(client, prompt):
                nonlocal calls
                self.assertTrue(client.reference_parts)
                calls += 1
                if calls == 4:
                    self.assertTrue(rendering.wait(5))
                if calls == 6:
                    next_prompt.set()
                return EDIT if calls % 3 == 0 else YES

            def wait(client, *args, **kwargs):
                rendering.set()
                self.assertTrue(next_prompt.wait(5))
                return wait_image(client, *args, **kwargs)

            with (
                patch.object(LLMClient, "complete", complete),
                patch.object(ComfyClient, "wait_image", wait),
            ):
                result = run_batch(config)
            self.assertEqual(result["rendered"], 2)
            self.assertEqual(result["failed"], 0)


class RepairUITests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_and_monitor(self):
        config = settings(fixtures.ROOT / "glasses-repair.yaml")
        app = OutpaintApp(config, repair=True)
        with patch(
            "birthday_stickers.batch_repair.run_batch",
            return_value={"rendered": 0, "skipped": 2, "failed": 0},
        ):
            async with app.run_test(size=(120, 42)) as pilot:
                self.assertEqual(
                    {w.id for w in app.query(Input)},
                    {"source", "destination", "steps", "cfg", "jpeg_quality"},
                )
                await pilot.click("#start")
                await app.workers.wait_for_complete()
                self.assertEqual(app.query_one("#pages", ContentSwitcher).current, "monitor")
                self.assertFalse(app.busy)
                await pilot.click("#back")
                self.assertEqual(app.query_one("#pages", ContentSwitcher).current, "setup")
