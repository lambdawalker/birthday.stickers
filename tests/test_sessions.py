import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_pipeline as fixtures

from birthday_stickers.comfy import ComfyClient
from birthday_stickers.control import RunControl, RunHalted
from birthday_stickers.pipeline import run
from birthday_stickers.sessions import SessionStore, session_lock


class SessionTests(unittest.TestCase):
    def config(self, temp, service, count=1):
        return fixtures.PipelineTests().configuration(temp, service, count)

    def test_snapshot_names_pause_and_resume_without_replanning(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(temp, service, 2)
            store = SessionStore(Path(temp) / "sessions")
            first = store.create(config)
            second = store.create(config)
            self.assertRegex(first["name"], r"^\d{4}-\d{2}-\d{2}-001$")
            self.assertTrue(second["name"].endswith("-002"))
            config.planning.write_text("CHANGED AFTER SNAPSHOT", encoding="utf-8")
            control = RunControl()

            def pause(item):
                if item.get("stage") == "refining":
                    control.request()

            result = store.execute(first["name"], control, pause)
            self.assertEqual(result["status"], "paused")
            self.assertEqual(len(service.chats), 1)
            self.assertNotIn("CHANGED AFTER SNAPSHOT", str(service.chats))
            # Reopening the store simulates a fresh invocation.
            store = SessionStore(store.root)
            result = store.execute(first["name"], RunControl())
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["completed"], 2)
            self.assertEqual(len(service.chats), 4)
            self.assertEqual(len(service.graphs), 4)
            self.assertEqual([x["name"] for x in store.list_incomplete()], [second["name"]])

    def test_pause_queued_job_reuses_prompt_id_on_resume(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(temp, service)
            control = RunControl()
            original = ComfyClient.wait_image

            def pause(client, prompt_id, output_id, checkpoint=lambda: None):
                control.request()
                return original(client, prompt_id, output_id, checkpoint)

            with patch.object(ComfyClient, "wait_image", pause), self.assertRaises(RunHalted):
                run(config, resume=True, control=control)
            self.assertEqual(len(service.graphs), 1)
            meta = json.loads(
                (config.output.directory / "metadata/stiker-0000.json").read_text(encoding="utf-8")
            )
            self.assertEqual(meta["qwen_prompt_id"], "job-1")
            result = run(config, resume=True)
            self.assertEqual(len(result), 1)
            self.assertEqual(len(service.graphs), 2)
            self.assertEqual(len(service.chats), 2)

    def test_stop_after_intermediate_skips_completed_stages_and_stickers(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(temp, service, 2)
            control = RunControl()

            def stop(done, name, stage):
                if name == "stiker-0001" and stage == "rendering_flux":
                    control.request("stopped")

            with self.assertRaises(RunHalted):
                run(config, resume=True, control=control, progress=stop)
            self.assertEqual(len(service.graphs), 3)
            result = run(config, resume=True)
            self.assertEqual(len(result), 2)
            self.assertEqual(len(service.graphs), 4)
            self.assertEqual(len(service.chats), 4)
            self.assertEqual(len(list((config.output.directory / "final").glob("*.jpg"))), 2)

    def test_unknown_submission_is_not_automatically_repeated(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = self.config(temp, service)
            service.fail_submit = True
            with self.assertRaises(RuntimeError):
                run(config, resume=True)
            service.fail_submit = False
            with self.assertRaisesRegex(RuntimeError, "outcome is unknown"):
                run(config, resume=True)
            self.assertEqual(len(service.graphs), 1)

    def test_lock_and_corrupt_session_reporting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with session_lock(root):
                with self.assertRaisesRegex(ValueError, "another process"):
                    with session_lock(root):
                        pass
            # Released locks are reusable (no stale marker removal needed).
            with session_lock(root):
                pass
            broken = root / "broken"
            broken.mkdir()
            (broken / "session.json").write_text("{", encoding="utf-8")
            store = SessionStore(root)
            self.assertEqual(store.list_incomplete(), [])
            self.assertTrue(store.errors)
            with self.assertRaises(ValueError):
                store.directory("../escape")
