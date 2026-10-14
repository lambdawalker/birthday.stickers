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
from birthday_stickers.flows import expand_config
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
            self.assertEqual(meta["generation_type"], "single_character_text")

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

    def test_dragon_outpaint_uses_raw_prompt_and_resumes_flux_job(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = load_config(fixtures.ROOT / "config.dragon.yaml")
            config.llm.base_url = service.url + "/v1"
            config.comfy.base_url = service.url
            config.comfy.poll_interval = 0.002
            config.generation.width = config.generation.height = 32
            config.generation.count = 1
            config.generation.outpainting = True
            config.output.width, config.output.height = 53, 55
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            control = RunControl()
            original = ComfyClient.wait_image

            def pause(client, prompt_id, output_id, checkpoint=lambda: None):
                if output_id == "9":
                    control.request()
                return original(client, prompt_id, output_id, checkpoint)

            with patch.object(ComfyClient, "wait_image", pause):
                result = store.execute(session["name"], control)
            self.assertEqual(result["status"], "paused", result.get("error"))
            result = store.execute(session["name"], RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            self.assertTrue(result["outpainting"])
            self.assertEqual(len(service.chats), 2)
            self.assertEqual(len(service.graphs), 2)
            self.assertEqual(len(service.uploads), 1)
            self.assertEqual(
                service.graphs[1]["23"]["inputs"]["text"],
                "One baby dragon curls up beside a pastel Viking longhouse.\n\n"
                + config.outpainting.read_text(encoding="utf-8").strip(),
            )
            self.assertEqual(service.graphs[1]["3"]["inputs"]["steps"], 50)
            self.assertEqual(service.graphs[1]["26"]["inputs"]["guidance"], 30)
            with Image.open(store.directory(session["name"]) / "final/stiker-0000.jpg") as image:
                self.assertEqual(image.size, (53, 55))

    def test_duo_can_disable_outpainting_without_flux_files(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            config.generation.outpainting = False
            config.flux.path = Path(temp) / "missing.json"
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            result = store.execute(session["name"], RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            self.assertFalse(result["outpainting"])
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(len(service.uploads), 2)
            with Image.open(store.directory(session["name"]) / "final/stiker-0000.jpg") as image:
                self.assertEqual(image.size, (32, 32))

    def test_legacy_configs_keep_their_original_outpainting_behavior(self):
        import yaml

        with tempfile.TemporaryDirectory() as temp:
            for filename, enabled in (("config.yaml", True), ("config.dragon.yaml", False)):
                data = expand_config(
                    yaml.safe_load((fixtures.ROOT / filename).read_text(encoding="utf-8")),
                    fixtures.ROOT,
                )
                data["generation"].pop("outpainting")
                data["generation"]["type"] = "viking_duo" if enabled else "baby_dragon"
                if not enabled:
                    data["workflows"].pop("flux")
                path = Path(temp) / filename
                path.write_text(yaml.safe_dump(data), encoding="utf-8")
                config = load_config(path)
                self.assertEqual(config.generation.outpainting, enabled)
                self.assertEqual(config.final_size, (1624, 1624) if enabled else (768, 1024))
