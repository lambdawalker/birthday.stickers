import tempfile
import unittest
from pathlib import Path

import test_pipeline as fixtures
import yaml

from birthday_stickers.config import load_config
from birthday_stickers.flows import ROOT, expand_config, get_flow, legacy_asset, resolve_asset
from birthday_stickers.sessions import SessionStore


class FlowTests(unittest.TestCase):
    def test_flow_first_common_fallback_and_clear_missing_error(self):
        with tempfile.TemporaryDirectory() as temp:
            flow = Path(temp) / "flow"
            common = Path(temp) / "common"
            flow.mkdir()
            common.mkdir()
            shared = common / "outpainting.md"
            shared.write_text("Shared instructions", encoding="utf-8")
            self.assertEqual(resolve_asset("outpainting.md", flow, common), shared.resolve())
            own = flow / "outpainting.md"
            own.write_text("Flow-specific instructions", encoding="utf-8")
            self.assertEqual(resolve_asset("outpainting.md", flow, common), own.resolve())
            own.unlink()
            shared.unlink()
            with self.assertRaisesRegex(ValueError, "searched.*flow.*common"):
                resolve_asset("outpainting.md", flow, common)

    def test_registry_aliases_and_defaults_with_partial_overrides(self):
        self.assertEqual(get_flow("viking_duo").name, "multi_character_reference")
        self.assertEqual(get_flow("baby_dragon").name, "single_character_text")
        self.assertTrue(get_flow("viking_duo").uses_references)
        self.assertFalse(get_flow("baby_dragon").uses_references)
        data = {
            "flow": "single_character_text",
            "workflows": {"flux": {"overrides": {"3": {"cfg": 1.2}}}},
        }
        expanded = expand_config(data, Path.cwd())
        self.assertEqual(expanded["workflows"]["flux"]["overrides"]["3"], {"steps": 50, "cfg": 1.2})
        self.assertEqual(Path(expanded["prompts"]["outpainting"]), ROOT / "common/outpainting.md")
        self.assertNotIn("generation", data)
        disabled = expand_config({"flow": "baby_dragon", "prompts": {"outpainting": None}}, ROOT)
        self.assertIsNone(disabled["prompts"]["outpainting"])

    def test_explicit_prompt_paths_and_legacy_redirects(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            data = expand_config(
                {"flow": "viking_duo", "prompts": {"planning": "./custom/planning.md"}}, base
            )
            self.assertEqual(
                Path(data["prompts"]["planning"]), (base / "custom/planning.md").resolve()
            )
            old = "prompts/plan_scene.md"
            self.assertEqual(
                legacy_asset(base, old), ROOT / "multi_character_reference/planning.md"
            )
            for spelling in ("./" + old, str(base / old), str(ROOT.parents[2] / old)):
                self.assertEqual(
                    legacy_asset(base, spelling), ROOT / "multi_character_reference/planning.md"
                )
            custom = base / old
            custom.parent.mkdir()
            custom.write_text("Existing customization", encoding="utf-8")
            self.assertEqual(legacy_asset(base, old), custom.resolve())

    def test_session_snapshots_file_prompt_and_converts_legacy_inline_text(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            source = Path(temp) / "outpainting.md"
            source.write_text("Original shared prompt", encoding="utf-8")
            config.outpainting = source
            store = SessionStore(Path(temp) / "sessions")
            item = store.create(config)
            source.write_text("Later edit", encoding="utf-8")
            saved = load_config(store.directory(item["name"]) / "config.yaml")
            self.assertEqual(
                saved.outpainting.read_text(encoding="utf-8"), "Original shared prompt"
            )
            snapshot = yaml.safe_load(saved.path.read_text(encoding="utf-8"))
            self.assertNotIn("outpainting_prompt", snapshot["output"])
            self.assertNotIn("flow", snapshot)
            config.outpainting = None
            config.output.outpainting_prompt = "Legacy inline prompt"
            legacy = store.create(config)
            saved = load_config(store.directory(legacy["name"]) / "config.yaml")
            self.assertEqual(saved.outpainting.read_text(encoding="utf-8"), "Legacy inline prompt")

    def test_protagonist_reference_text_dragon_end_to_end_and_snapshot(self):
        import json

        from PIL import Image

        from birthday_stickers.pipeline import run
        from birthday_stickers.references import reference_pools

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            data = yaml.safe_load(config.path.read_text(encoding="utf-8"))
            data["flow"] = "protagonist_reference_creature_text"
            data["generation"].pop("type", None)
            data.pop("workflows")
            data.pop("prompts")
            characters = Path(temp) / "characters"
            characters.mkdir()
            Image.new("RGB", (32, 32), "green").save(characters / "A789.png")
            data["characters"] = {"directory": str(characters), "protagonist": "A"}
            data["generation"]["cfg"] = 2.5
            config.path.write_text(yaml.safe_dump(data), encoding="utf-8")
            config = load_config(config.path)
            self.assertEqual(config.flow.reference_roles, ("protagonist",))
            self.assertEqual(reference_pools(config)[1], [])
            result = run(config)
            self.assertEqual(len(result), 1)
            graph = service.graphs[0]
            loaders = [node for node in graph.values() if node["class_type"] == "LoadImage"]
            self.assertEqual(len(loaders), 1)
            self.assertNotIn("516", graph)
            encoder = graph["459:474"]["inputs"]
            self.assertNotIn("images.image_2", encoder)
            self.assertNotIn("<image2>", encoder["prompt"])
            self.assertEqual(graph["459:458"]["inputs"]["cfg"], 2.5)
            self.assertEqual(len(service.graphs), 2)  # Qwen, then outpainting.
            metadata = json.loads(
                (config.output.directory / "metadata/stiker-0000.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(metadata["references"]), 1)
            self.assertEqual(Path(metadata["references"][0]["path"]).name, "A789.jpg")
            run(config, resume=True)
            self.assertEqual(len(service.graphs), 2)
            store = SessionStore(Path(temp) / "sessions")
            item = store.create(config)
            saved = load_config(store.directory(item["name"]) / "config.yaml")
            self.assertEqual(saved.flow.reference_roles, ("protagonist",))
            self.assertEqual(saved.creature, "")
