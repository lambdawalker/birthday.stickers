import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_pipeline as fixtures
from PIL import Image

from birthday_stickers.activities import load_activities
from birthday_stickers.comfy import ComfyClient
from birthday_stickers.config import load_config
from birthday_stickers.control import RunControl
from birthday_stickers.engine import Pipeline, Scene
from birthday_stickers.pipeline import reserve_sticker, validate_local
from birthday_stickers.references import discover_characters
from birthday_stickers.sessions import SessionStore


class GenerationTypeTests(unittest.TestCase):
    def test_dragon_session_pause_resume_only_one_render_no_references(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = load_config(fixtures.ROOT / "config.dragon.yaml")
            config.llm.base_url = service.url + "/v1"
            config.comfy.base_url = service.url
            config.comfy.poll_interval = 0.01
            config.generation.width = config.generation.height = 32
            config.output.width = config.output.height = 32
            config.generation.count = 1
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            directory = store.directory(session["name"])
            control = RunControl()
            original = ComfyClient.wait_image

            def pause(client, prompt_id, output_id, checkpoint=lambda: None):
                control.request()
                return original(client, prompt_id, output_id, checkpoint)

            with patch.object(ComfyClient, "wait_image", pause):
                result = store.execute(session["name"], control)
            self.assertEqual(result["status"], "paused", result.get("error"))
            result = store.execute(session["name"], RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            self.assertEqual(len(service.chats), 2)
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(service.uploads, [])
            graph = service.graphs[0]
            self.assertNotIn("459:471", graph)
            self.assertNotIn("13", graph)
            self.assertNotIn("484", graph)
            self.assertEqual(
                graph["459:452"]["inputs"]["prompt"],
                "One baby dragon curls up beside a pastel Viking longhouse.",
            )
            with Image.open(directory / "final/stiker-0000.jpg") as image:
                self.assertEqual(image.size, (32, 32))
            self.assertFalse(list((directory / "intermediate").glob("*.jpg")))
            self.assertFalse((directory / "inputs/characters").exists())
            meta = json.loads((directory / "metadata/stiker-0000.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["references"], [])
            self.assertEqual(meta["generation_type"], "baby_dragon")

    def test_live_character_folder_and_unlimited_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            folder = Path(temp) / "characters"
            folder.mkdir()
            for name in ("A0.jpg", "B100.jpg", "C123456.jpg"):
                Image.new("RGB", (32, 32)).save(folder / name)
            config.character_directory = folder
            config.protagonist, config.creature = "A", "C"
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            saved = load_config(store.directory(session["name"]) / "config.yaml")
            self.assertEqual(saved.character_directory, folder.resolve())
            self.assertFalse((store.directory(session["name"]) / "inputs/characters").exists())
            pipeline = Pipeline(saved, RunControl(), lambda *_: None, lambda *_: None)
            pipeline.local = validate_local(saved)
            Image.new("RGB", (32, 32)).save(folder / "A999999.jpg")
            meta = {}
            with patch("birthday_stickers.engine.secrets.choice", side_effect=lambda p: p[-1]):
                pipeline.prepare_references(meta, 0)
            self.assertEqual(Path(meta["references"][0]["path"]).name, "A999999.jpg")
            self.assertEqual(set(discover_characters(folder)), {"A", "B", "C"})

    def test_dragon_catalog_and_graph(self):
        config = load_config(fixtures.ROOT / "config.dragon.yaml")
        rows = load_activities(config.activities)
        self.assertEqual(len(rows), 300)
        self.assertEqual(len({r["activity"] for r in rows}), 300)
        self.assertEqual(len(validate_local(config)["graphs"]), 1)

    def test_changed_reference_is_uploaded_again_for_new_scene(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, 2)
            pipeline = Pipeline(config, RunControl(), lambda *_: None, lambda *_: None)
            pipeline.local = validate_local(config)
            pipeline.comfy = ComfyClient(config.comfy)
            for index in range(2):
                if index:
                    Image.new("RGB", (32, 32), "purple").save(config.protagonist)
                _, name, path = reserve_sticker(config.output.directory)
                meta = {"sticker": name, "raw_scene": "A scene", "refined_scene": "A scene"}
                pipeline.prepare_references(meta, index)
                Scene(pipeline, path, meta, "images").render()
            # Two initial references, two intermediates, one changed protagonist.
            self.assertEqual(len(service.uploads), 5)
