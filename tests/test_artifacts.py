import json
import tempfile
import unittest
from pathlib import Path

import test_pipeline as fixtures

from birthday_stickers.artifacts import STAGES, result_path
from birthday_stickers.control import RunControl
from birthday_stickers.pipeline import prepare_records, run
from birthday_stickers.sessions import SessionStore


class ArtifactTests(unittest.TestCase):
    def test_add_outpainting_to_completed_session_preserves_originals_and_logs(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=2)
            config.generation.outpainting = False
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            name = session["name"]
            self.assertEqual(store.execute(name, RunControl())["status"], "complete")
            root = store.directory(name)
            originals = {
                p.name: p.read_bytes() for p in (root / "image_generation/results").glob("*.jpg")
            }
            self.assertEqual(len(originals), 2)
            self.assertFalse(list((root / "outpainting/results").glob("*.jpg")))
            log = root / "scene_planning/logs/stiker-0000.jsonl"
            previous_log = log.read_bytes()
            store.enable_outpainting(name, config)
            result = store.execute(name, RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            self.assertEqual(len(service.chats), 4)
            self.assertEqual(len(service.graphs), 4)
            self.assertEqual(len(list((root / "outpainting/results").glob("*.jpg"))), 2)
            for filename, content in originals.items():
                self.assertEqual(
                    (root / "image_generation/results" / filename).read_bytes(), content
                )
            self.assertTrue(log.read_bytes().startswith(previous_log))
            for stage in STAGES.values():
                self.assertTrue((root / stage / "logs").is_dir())
                events = [
                    json.loads(line)
                    for line in (root / stage / "logs/stiker-0000.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                self.assertTrue(any("seconds" in event for event in events))
            store.enable_outpainting(name, config)
            store.execute(name, RunControl())
            self.assertEqual(len(service.graphs), 4)

    def test_legacy_generated_image_can_be_outpainted_without_recreation(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            config.generation.outpainting = False
            original = run(config)[0]
            root = config.output.directory
            content = original.read_bytes()
            (root / "final").mkdir()
            original.replace(root / "final/stiker-0000.jpg")
            metadata = root / "metadata/stiker-0000.json"
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            meta["generation_type"] = "baby_dragon"
            meta.pop("outpainting", None)
            meta.pop("outpaint_skipped", None)
            meta.pop("artifact_layout", None)
            meta.pop("image_saved", None)
            meta.pop("intermediate_saved", None)
            metadata.write_text(json.dumps(meta), encoding="utf-8")
            config.generation.outpainting = True
            prepare_records(config)
            prepare_records(config)
            self.assertFalse(result_path(root, "outpainting", "stiker-0000").exists())
            result = run(config, resume=True)
            self.assertEqual(result[0], result_path(root, "outpainting", "stiker-0000"))
            self.assertEqual(result_path(root, "images", "stiker-0000").read_bytes(), content)
            self.assertEqual((root / "final/stiker-0000.jpg").read_bytes(), content)
            self.assertEqual(len(service.chats), 2)
            self.assertEqual(len(service.graphs), 2)

    def test_enable_is_noop_for_legacy_duo_with_implicit_outpainting(self):
        import yaml

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            path = store.directory(session["name"]) / "config.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["generation"].pop("outpainting")
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            before = path.read_bytes()
            config.output.width = 999
            store.enable_outpainting(session["name"], config)
            self.assertEqual(path.read_bytes(), before)

    def test_disable_then_reenable_preserves_finished_outpainting(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            name = store.create(config)["name"]
            self.assertEqual(store.execute(name, RunControl())["status"], "complete")
            output = result_path(store.directory(name), "outpainting", "stiker-0000")
            original = output.read_bytes()
            store.set_outpainting(name, False, config)
            self.assertEqual(store.execute(name, RunControl())["status"], "complete")
            self.assertEqual(output.read_bytes(), original)
            config.output.width = 999  # Re-enabling must retain the existing stage's settings.
            store.set_outpainting(name, True, config)
            self.assertEqual(store.execute(name, RunControl())["status"], "complete")
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(len(service.graphs), 2)
            self.assertEqual(len(service.chats), 2)

    def test_disable_pending_outpaint_then_reenable_reuses_job(self):
        from unittest.mock import patch

        from birthday_stickers.comfy import ComfyClient

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=2)
            store = SessionStore(Path(temp) / "sessions")
            name = store.create(config)["name"]
            control = RunControl()
            original_wait = ComfyClient.wait_image

            def wait(client, *args, **kwargs):
                if client.worker == "outpainting":
                    control.request("paused")
                    control.check()
                return original_wait(client, *args, **kwargs)

            with patch.object(ComfyClient, "wait_image", wait):
                self.assertEqual(store.execute(name, control)["status"], "paused")
            store.set_outpainting(name, False, config)
            order = []

            def track_wait(client, *args, **kwargs):
                order.append(client.worker)
                return original_wait(client, *args, **kwargs)

            original_terminal = ComfyClient.wait_terminal

            def track_terminal(client, *args, **kwargs):
                order.append(client.worker)
                return original_terminal(client, *args, **kwargs)

            with (
                patch.object(ComfyClient, "wait_image", track_wait),
                patch.object(ComfyClient, "wait_terminal", track_terminal),
            ):
                self.assertEqual(store.execute(name, RunControl())["status"], "complete")
            self.assertEqual(order, ["outpainting", "images"])
            store.set_outpainting(name, True, config)
            self.assertEqual(store.execute(name, RunControl())["status"], "complete")
            self.assertEqual(len(service.graphs), 4)

    def test_disabled_stage_drain_accepts_terminal_failure_but_not_network_failure(self):
        from unittest.mock import patch

        from birthday_stickers.comfy import ComfyClient
        from birthday_stickers.http import ServiceError

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            client = ComfyClient(config.comfy, "outpainting")
            failed = {
                "job-1": {
                    "status": {
                        "status_str": "error",
                        "messages": [["execution_error", {"exception_message": "GPU OOM"}]],
                    }
                }
            }
            with patch.object(client.http, "json", return_value=failed):
                client.wait_terminal("job-1")
            with patch.object(client.http, "json", side_effect=ServiceError("offline")):
                with self.assertRaisesRegex(ServiceError, "offline"):
                    client.wait_terminal("job-1")
