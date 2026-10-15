import json
import tempfile
import unittest
from pathlib import Path

import test_pipeline as fixtures

from birthday_stickers.artifacts import result_path
from birthday_stickers.control import RunControl
from birthday_stickers.sessions import SessionStore


class RegenerationTests(unittest.TestCase):
    def test_deleted_generated_image_invalidates_outpainting_and_changes_both_seeds(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=2)
            store = SessionStore(Path(temp) / "sessions")
            name = store.create(config)["name"]
            self.assertEqual(store.execute(name, RunControl())["status"], "complete")
            root = store.directory(name)
            path = root / "metadata/stiker-0000.json"
            before = json.loads(path.read_text(encoding="utf-8"))
            untouched = (root / "metadata/stiker-0001.json").read_bytes()
            result_path(root, "images", "stiker-0000").unlink()
            result = store.execute(name, RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotEqual(before["qwen_seed"], after["qwen_seed"])
            self.assertNotEqual(before["flux_seed"], after["flux_seed"])
            self.assertEqual(before["raw_scene"], after["raw_scene"])
            self.assertEqual(before["references"], after["references"])
            self.assertEqual(len(service.chats), 4)
            self.assertEqual(len(service.graphs), 6)
            self.assertEqual((root / "metadata/stiker-0001.json").read_bytes(), untouched)
            self.assertEqual(len(list((root / "outpainting/superseded").glob("*.jpg"))), 1)

    def test_deleted_outpainting_only_reruns_flux(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            name = store.create(config)["name"]
            store.execute(name, RunControl())
            root = store.directory(name)
            path = root / "metadata/stiker-0000.json"
            before = json.loads(path.read_text(encoding="utf-8"))
            image = result_path(root, "images", "stiker-0000").read_bytes()
            result_path(root, "outpainting", "stiker-0000").unlink()
            result = store.execute(name, RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(before["qwen_prompt_id"], after["qwen_prompt_id"])
            self.assertNotEqual(before["flux_seed"], after["flux_seed"])
            self.assertEqual(result_path(root, "images", "stiker-0000").read_bytes(), image)
            self.assertEqual(len(service.graphs), 3)
            self.assertEqual(len(service.chats), 2)

    def test_invalidation_is_idempotent_and_deferred_outpainting_stays_invalid(self):
        from birthday_stickers.config import load_config
        from birthday_stickers.pipeline import prepare_records

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            name = store.create(config)["name"]
            store.execute(name, RunControl())
            root = store.directory(name)
            result_path(root, "images", "stiker-0000").unlink()
            store.set_outpainting(name, False, config)
            saved = load_config(root / "config.yaml")
            first = prepare_records(saved)[0][1]
            second = prepare_records(saved)[0][1]
            self.assertEqual(first["qwen_seed"], second["qwen_seed"])
            self.assertEqual(len(second["regenerations"]), 1)
            self.assertFalse(result_path(root, "outpainting", "stiker-0000").exists())
            result = store.execute(name, RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            self.assertEqual(len(service.graphs), 3)
            store.set_outpainting(name, True, config)
            result = store.execute(name, RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            self.assertEqual(len(service.graphs), 4)
            self.assertEqual(len(service.chats), 2)

    def test_missing_unfinished_output_reuses_existing_job_and_seed(self):
        from unittest.mock import patch

        from birthday_stickers.comfy import ComfyClient

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            config.generation.outpainting = False
            store = SessionStore(Path(temp) / "sessions")
            name = store.create(config)["name"]
            control = RunControl()

            def pause(*args, **kwargs):
                control.request("paused")
                control.check()

            with patch.object(ComfyClient, "wait_image", pause):
                self.assertEqual(store.execute(name, control)["status"], "paused")
            path = store.directory(name) / "metadata/stiker-0000.json"
            before = json.loads(path.read_text(encoding="utf-8"))
            result = store.execute(name, RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(before["qwen_seed"], after["qwen_seed"])
            self.assertEqual(before["qwen_prompt_id"], after["qwen_prompt_id"])
            self.assertNotIn("regenerations", after)
            self.assertEqual(len(service.graphs), 1)

    def test_deleting_base_with_pending_flux_drains_old_job_before_regeneration(self):
        from unittest.mock import patch

        from birthday_stickers.comfy import ComfyClient

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            name = store.create(config)["name"]
            control = RunControl()
            original = ComfyClient.wait_image

            def pause(client, *args, **kwargs):
                if client.worker == "outpainting":
                    control.request("paused")
                    control.check()
                return original(client, *args, **kwargs)

            with patch.object(ComfyClient, "wait_image", pause):
                self.assertEqual(store.execute(name, control)["status"], "paused")
            result_path(store.directory(name), "images", "stiker-0000").unlink()
            terminal = ComfyClient.wait_terminal
            with patch.object(
                ComfyClient, "wait_terminal", autospec=True, side_effect=terminal
            ) as drained:
                result = store.execute(name, RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            self.assertEqual(drained.call_count, 1)
            self.assertEqual(len(service.graphs), 4)
            self.assertEqual(len(service.chats), 2)
