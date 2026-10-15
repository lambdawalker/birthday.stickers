import tempfile
import unittest
from pathlib import Path

import test_pipeline as fixtures

from birthday_stickers.config import load_config
from birthday_stickers.images import plan_padding
from birthday_stickers.sessions import SessionStore
from birthday_stickers.workflows import build_flux, load_workflow


class NegativePromptTests(unittest.TestCase):
    def test_all_presets_receive_shared_negative_and_preserve_existing_text(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("config.yaml", "config.dragon.yaml", "config.reference-dragon.yaml"):
            config = load_config(root / filename)
            template = load_workflow(config.qwen.path)
            template[str(config.qwen.nodes["encoder"])]["inputs"]["negative_prompt"] = "existing"
            graph = config.flow.build_graph(
                config,
                template,
                "A scene",
                tuple(f"ref{i}.jpg" for i in config.reference_roles),
                1,
                "test",
            )
            encoder = graph[str(config.qwen.nodes["encoder"])]
            for term in ("border", "3d", "incomplete image", "framed"):
                self.assertIn(term, encoder["inputs"]["negative_prompt"])
            sampler = graph[str(config.qwen.nodes["sampler"])]["inputs"]
            self.assertEqual(sampler["negative"], [str(config.qwen.nodes["encoder"]), 1])
            self.assertGreater(sampler["cfg"], 1)
            self.assertIn("existing", encoder["inputs"]["negative_prompt"])

    def test_flux_negative_reaches_sampler_through_inpaint_conditioning(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
        graph = build_flux(
            load_workflow(config.flux.path),
            config.flux.nodes,
            config.flux.overrides,
            "Scene",
            "source.jpg",
            plan_padding((32, 32), (64, 64)),
            4,
            1,
            "test",
            negative="border, 3d, incomplete image, framed.",
        )
        conditioning = graph[str(config.flux.nodes["conditioning"])]["inputs"]
        node = graph[conditioning["negative"][0]]
        self.assertEqual(node["class_type"], "CLIPTextEncode")
        self.assertEqual(node["inputs"]["text"], "border, 3d, incomplete image, framed.")
        self.assertNotIn("46", graph)  # Zero-out would discard the requested conditioning.
        self.assertEqual(graph[str(config.flux.nodes["sampler"])]["inputs"]["cfg"], 1)

    def test_session_snapshots_negative_file(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            saved = load_config(store.directory(session["name"]) / "config.yaml")
            self.assertEqual(
                saved.negative.read_text(encoding="utf-8"),
                config.negative.read_text(encoding="utf-8"),
            )
            self.assertEqual(saved.negative.parent.name, "inputs")
