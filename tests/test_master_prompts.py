import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_pipeline as fixtures
from PIL import Image

from birthday_stickers.control import RunControl, RunHalted
from birthday_stickers.engine import Pipeline, Scene
from birthday_stickers.llm import LLMClient
from birthday_stickers.pipeline import reserve_sticker, validate_local
from birthday_stickers.presets import ROOT, with_preset


def setup(temp, service):
    config = with_preset(
        fixtures.PipelineTests().configuration(temp, service), ROOT / "girl_reference_dragon_text"
    )
    config.character_directory = Path(temp) / "characters"
    config.character_directory.mkdir()
    Image.new("RGB", (32, 32)).save(config.character_directory / "A12.jpg")
    return config


def scene_for(config):
    pipeline = Pipeline(config, RunControl(), lambda *_: None, lambda *_: None)
    pipeline.local = validate_local(config)
    _, name, path = reserve_sticker(config.output.directory)
    return Scene(pipeline, path, {"sticker": name}, "prompts")


class MasterPromptTests(unittest.TestCase):
    def test_master_templates_render_data_once_and_survive_preset_switch(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = setup(temp, service)
            config.planning = Path(temp) / "planning.md"
            config.planning.write_text(
                "CUSTOM PLAN\n{{scene_brief}}\n{{previous_scenes}}", encoding="utf-8"
            )
            config.refinement = Path(temp) / "refinement.md"
            config.refinement.write_text(
                "CUSTOM REFINE\n{{guide}}\n{{scene}}\n{{character_sources}}", encoding="utf-8"
            )
            switched = with_preset(config, ROOT / "girl_dragon_references")
            self.assertEqual(switched.planning, config.planning)
            self.assertEqual(switched.refinement, config.refinement)
            scene = scene_for(config)
            requests = []

            def complete(client, prompt, *, refined=False):
                requests.append(prompt)
                return "Girl <image1> and dragon {{ literal }}." if refined else "RAW {{ literal }}"

            with patch.object(LLMClient, "complete", complete):
                scene.prepare(1, ["Earlier scene"])
            self.assertTrue(requests[0].startswith("CUSTOM PLAN"))
            self.assertIn("Earlier scene", requests[0])
            self.assertIn("Character girl", requests[0])
            self.assertTrue(requests[1].startswith("CUSTOM REFINE"))
            self.assertIn("RAW {{ literal }}", requests[1])
            self.assertIn("<image1>", requests[1])
            self.assertNotIn("{{character_sources}}", requests[1])

    def test_missing_or_extra_tags_are_corrected_before_prompt_is_ready(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            scene = scene_for(setup(temp, service))
            responses = iter(
                [
                    "A girl and baby dragon bake.",
                    "A girl and baby dragon bake.",
                    "Girl <image1> and dragon <image2> bake.",
                    "Girl <image1> and a text-described baby dragon bake.",
                ]
            )
            requests = []

            def complete(client, prompt, *, refined=False):
                requests.append(prompt)
                return next(responses)

            with (
                patch.object(LLMClient, "complete", complete),
                patch("birthday_stickers.retry.pause"),
            ):
                scene.prepare(1, [])
            saved = json.loads(scene.path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "prompts_ready")
            self.assertIn("<image1>", saved["refined_scene"])
            self.assertNotIn("<image2>", saved["refined_scene"])
            self.assertIn("Missing", requests[2])
            self.assertIn("Unexpected", requests[3])
            self.assertEqual(saved["raw_scene"], "A girl and baby dragon bake.")

    def test_pause_during_correction_keeps_raw_scene_and_rejected_response(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            scene = scene_for(setup(temp, service))
            with patch.object(LLMClient, "complete", side_effect=["RAW", "Missing the tag"]):
                with patch("birthday_stickers.retry.pause", side_effect=RunHalted("paused")):
                    with self.assertRaises(RunHalted), scene.guard():
                        scene.prepare(1, [])
            saved = json.loads(scene.path.read_text(encoding="utf-8"))
            self.assertEqual(saved["raw_scene"], "RAW")
            self.assertEqual(saved["refinement_candidate"], "Missing the tag")
            self.assertNotIn("refined_scene", saved)
            requests = []

            def complete(client, prompt, *, refined=False):
                self.assertTrue(refined)
                requests.append(prompt)
                return "Girl <image1> and baby dragon."

            with patch.object(LLMClient, "complete", complete):
                Scene(scene.pipeline, scene.path, saved, "prompts").prepare(1, [])
            self.assertIn("Missing", requests[0])
            self.assertEqual(
                json.loads(scene.path.read_text(encoding="utf-8"))["status"], "prompts_ready"
            )

    def test_invalid_master_is_rejected_before_services(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = setup(temp, service)
            config.planning = Path(temp) / "custom.md"
            for text, message in (
                ("Missing brief", "scene_brief"),
                ("{{ scene_brief }} {{ typo }}", "typo"),
            ):
                config.planning.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                    validate_local(config)
            self.assertEqual(service.chats, [])
            self.assertEqual(service.graphs, [])

    def test_session_snapshots_both_master_files(self):
        from birthday_stickers.config import load_config
        from birthday_stickers.sessions import SessionStore

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = setup(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            item = store.create(config)
            saved = load_config(store.directory(item["name"]) / "config.yaml")
            self.assertEqual(item["version"], 2)
            self.assertEqual(saved.planning.name, "planning.md")
            self.assertEqual(saved.refinement.name, "refinement.md")
            self.assertEqual(
                saved.planning.read_text(encoding="utf-8"),
                config.planning.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                saved.refinement.read_text(encoding="utf-8"),
                config.refinement.read_text(encoding="utf-8"),
            )
            self.assertIn("scene_brief", saved.planning.read_text(encoding="utf-8"))
            self.assertIn("character_sources", saved.refinement.read_text(encoding="utf-8"))

    def test_each_shipped_preset_owns_its_master_templates(self):
        from birthday_stickers.config import load_config

        for filename in ("config.yaml", "config.dragon.yaml", "config.reference-dragon.yaml"):
            config = load_config(fixtures.ROOT / filename)
            with self.subTest(config=filename):
                self.assertEqual(config.planning.parent, config.preset.directory)
                self.assertEqual(config.refinement.parent, config.preset.directory)
                self.assertTrue(config.planning.is_file())
                self.assertTrue(config.refinement.is_file())

    def test_switching_preset_switches_default_master_templates(self):
        from birthday_stickers.config import load_config

        config = load_config(fixtures.ROOT / "config.yaml")
        switched = with_preset(config, ROOT / "baby_dragon_text")
        self.assertEqual(switched.planning.parent, switched.preset.directory)
        self.assertEqual(switched.refinement.parent, switched.preset.directory)
        self.assertNotEqual(switched.planning, config.planning)
