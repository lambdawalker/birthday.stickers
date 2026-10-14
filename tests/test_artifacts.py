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
