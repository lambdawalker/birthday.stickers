import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_pipeline as fixtures
import yaml
from PIL import Image

from birthday_stickers.config import load_config
from birthday_stickers.control import RunControl, RunHalted
from birthday_stickers.engine import Pipeline, Scene
from birthday_stickers.llm import LLMClient
from birthday_stickers.pipeline import reserve_sticker, run, validate_local
from birthday_stickers.presets import ROOT, load_preset, render_scene, select_scene, with_preset
from birthday_stickers.references import reference_pools
from birthday_stickers.sessions import SessionStore
from birthday_stickers.workflows import load_workflow


def write_preset(folder, characters=None):
    folder.mkdir(parents=True, exist_ok=True)
    characters = characters or [
        {"id": "dragon", "name": "Baby dragon", "description": "Grey, small wings"},
        {"id": "girl", "name": "Princess", "imagePrefix": "girl"},
        {"id": "fox", "name": "Fox", "imagePrefix": "fox", "description": "Red scarf"},
    ]
    documents = {
        "characters": {"characters": characters},
        "activities": {
            "activities": [
                {
                    "name": "Baking",
                    "description": "{{ characters.dragon.name }} uses "
                    "{{ props.spoons | random }} with {{ characters.girl.name }}.",
                    "props": {"spoons": ["LOCAL SPOON"]},
                    "venueTags": ["kitchen"],
                }
            ]
        },
        "venues": {
            "venues": [
                {
                    "name": "Longhouse",
                    "description": "Inside a {{ props.building | random }}",
                    "tags": ["kitchen"],
                    "props": {
                        "building": ["VENUE HOUSE"],
                        "foreground": ["VENUE FRONT"],
                        "background": ["VENUE BACK"],
                    },
                }
            ]
        },
        "props": {"props": {"skyProp": ["GLOBAL SKY"], "spoons": ["WRONG SPOON"]}},
    }
    for name, data in documents.items():
        (folder / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    (folder / "image_style.md").write_text("STYLE FIRST", encoding="utf-8")
    (folder / "general_direction.md").write_text(
        "{{ activity.description }} {{ venue.description }}\n"
        "Place {{ venue.props.foreground | random }} in foreground.\n"
        "Place {{ venue.props.background | random }} in background.\n"
        "A {{ props.skyProp | random }} is in the right corner.",
        encoding="utf-8",
    )
    return folder


class PresetTests(unittest.TestCase):
    def test_template_scopes_order_and_reference_assignments(self):
        with tempfile.TemporaryDirectory() as temp:
            preset = load_preset(write_preset(Path(temp)))
            scene = render_scene(preset, preset.activities[0], preset.venues[0], 12)
            prompt = scene["prompt"]
            self.assertTrue(prompt.startswith("STYLE FIRST\n\nCharacter dragon"))
            self.assertLess(prompt.index("Character fox"), prompt.index("LOCAL SPOON"))
            for value in ("VENUE HOUSE", "VENUE FRONT", "VENUE BACK", "GLOBAL SKY"):
                self.assertIn(value, prompt)
            self.assertNotIn("WRONG SPOON", prompt)
            self.assertEqual(
                [c["imageTag"] for c in scene["characters"]], ["", "<image1>", "<image2>"]
            )
            self.assertEqual(
                scene, render_scene(preset, preset.activities[0], preset.venues[0], 12)
            )

    def test_strict_validation_and_cache_invalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = write_preset(Path(temp))
            load_preset(folder)
            (folder / "general_direction.md").write_text("{{ props.typo }}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "general_direction.md.*typo"):
                load_preset(folder)
            write_preset(folder)
            data = yaml.safe_load((folder / "activities.yaml").read_text(encoding="utf-8"))
            data["activities"][0]["props"]["spoons"] = []
            (folder / "activities.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty"):
                load_preset(folder)

    def test_invalid_characters_and_incompatible_venues(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for characters, error in (
                ([{"id": "same", "name": "a", "description": "x"}] * 2, "Duplicate"),
                ([{"id": "x", "name": "a"}], "description or imagePrefix"),
                ([{"id": "x", "name": "a", "imagePrefix": "../bad"}], "filename prefix"),
                (
                    [
                        {"id": "x", "name": "a", "imagePrefix": "A"},
                        {"id": "y", "name": "b", "imagePrefix": "A1"},
                    ],
                    "Overlapping",
                ),
                (
                    [{"id": f"c{i}", "name": "a", "imagePrefix": f"ref{i}_"} for i in range(11)],
                    "at most 10",
                ),
            ):
                write_preset(folder, characters)
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                    load_preset(folder)
            write_preset(folder)
            data = yaml.safe_load((folder / "activities.yaml").read_text(encoding="utf-8"))
            data["activities"][0]["venueTags"] = ["ocean"]
            (folder / "activities.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no compatible venue"):
                load_preset(folder)

    def test_bundled_migration_and_graph_reference_counts(self):
        for name in ("config.yaml", "config.dragon.yaml", "config.reference-dragon.yaml"):
            config = load_config(fixtures.ROOT / name)
            self.assertEqual(len(config.preset.activities), 300)
            self.assertEqual(config.generation.type, "scene_preset")
            graph = config.flow.build_graph(
                config,
                load_workflow(config.qwen.path),
                "scene",
                tuple(f"ref{i}.jpg" for i in config.reference_roles),
                123,
                "test",
            )
            self.assertEqual(
                sum(n["class_type"] == "LoadImage" for n in graph.values()),
                len(config.reference_roles),
            )
            encoder = graph[str(config.qwen.nodes["encoder"])]["inputs"]
            self.assertIn("border", encoder["negative_prompt"])
        config = load_config(fixtures.ROOT / "config.yaml")
        graph = config.flow.build_graph(
            config,
            load_workflow(config.qwen.path),
            "scene",
            tuple(f"ref{i}.jpg" for i in range(10)),
            123,
            "test",
        )
        encoder = graph[str(config.qwen.nodes["encoder"])]["inputs"]
        for i in range(1, 11):
            self.assertEqual(
                graph[encoder[f"images.image_{i}"][0]]["inputs"]["image"], f"ref{i - 1}.jpg"
            )

    def test_named_reference_conversion_and_live_session_snapshot(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            preset_folder = write_preset(Path(temp) / "preset")
            config = with_preset(config, preset_folder)
            config.character_directory = Path(temp) / "characters"
            config.character_directory.mkdir()
            for name in ("girl789.jpg", "girl789.png", "fox123456.jpg"):
                Image.new("RGB", (32, 32)).save(config.character_directory / name)
            pools = reference_pools(config)
            self.assertEqual({p.name for p in pools[0]}, {"girl789.jpg", "girl790.jpg"})
            self.assertEqual(reference_pools(config), pools)  # No repeated conversion.
            store = SessionStore(Path(temp) / "sessions")
            item = store.create(config)
            (preset_folder / "image_style.md").write_text("CHANGED STYLE", encoding="utf-8")
            saved = load_config(store.directory(item["name"]) / "config.yaml")
            self.assertEqual(saved.preset.image_style, "STYLE FIRST")
            self.assertEqual(
                saved.character_directory.resolve(), config.character_directory.resolve()
            )
            self.assertFalse((store.directory(item["name"]) / "inputs/characters").exists())
            Image.new("RGB", (32, 32)).save(config.character_directory / "fox999999.jpg")
            self.assertEqual(len(reference_pools(saved)[1]), 2)

    def test_without_replacement_sampling(self):
        with tempfile.TemporaryDirectory() as temp:
            preset = load_preset(write_preset(Path(temp) / "preset"))
            second = copy.deepcopy(preset.activities[0])
            second["id"] = "other"
            preset.activities.append(second)
            metadata = Path(temp) / "metadata"
            metadata.mkdir()
            selections = []
            for i in range(4):
                scene = select_scene(preset, metadata)
                selections.append(scene["activity"]["id"])
                (metadata / f"stiker-{i:04}.json").write_text(
                    json.dumps({"scene_selection": scene}), encoding="utf-8"
                )
            self.assertEqual(len(set(selections[:2])), 2)
            self.assertEqual(len(set(selections[2:])), 2)

    def test_failed_planning_reuses_saved_selection_on_resume(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = with_preset(
                fixtures.PipelineTests().configuration(temp, service), ROOT / "baby_dragon_text"
            )
            pipeline = Pipeline(config, RunControl(), lambda *_: None, lambda *_: None)
            pipeline.local = validate_local(config)
            _, name, path = reserve_sticker(config.output.directory)
            scene = Scene(pipeline, path, {"sticker": name}, "prompts")
            with patch.object(LLMClient, "complete", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    scene.prepare(0, [])
            saved = json.loads(path.read_text(encoding="utf-8"))
            with patch(
                "birthday_stickers.presets.select_scene", side_effect=AssertionError("reroll")
            ):
                with patch.object(
                    LLMClient, "complete", return_value="A baby dragon sleeps."
                ) as llm:
                    Scene(pipeline, path, saved, "prompts").prepare(0, [])
                    self.assertEqual(llm.call_args_list[0].args[0], saved["planning_request"])

    def test_mixed_preset_runs_all_three_stages_and_resumes(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = with_preset(
                fixtures.PipelineTests().configuration(temp, service),
                write_preset(Path(temp) / "preset"),
            )
            config.character_directory = Path(temp) / "characters"
            config.character_directory.mkdir()
            for name in ("girl1.jpg", "fox2.jpg"):
                Image.new("RGB", (32, 32)).save(config.character_directory / name)
            with patch.object(
                LLMClient,
                "complete",
                side_effect=[
                    "Baby dragon bakes with princess and fox.",
                    "Baby dragon bakes with princess <image1> and fox <image2>.",
                ],
            ) as llm:
                run(config)
                run(config, resume=True)
                self.assertEqual(llm.call_count, 2)
            self.assertEqual(len(service.graphs), 2)
            graph = service.graphs[0]
            encoder = graph[str(config.qwen.nodes["encoder"])]["inputs"]
            self.assertIn("images.image_2", encoder)
            self.assertNotIn("images.image_3", encoder)
            self.assertTrue(
                (config.output.directory / "outpainting/results/stiker-0000.jpg").is_file()
            )

    def test_pause_during_selection_recovers_without_changing_references_or_seed(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = with_preset(
                fixtures.PipelineTests().configuration(temp, service),
                ROOT / "girl_reference_dragon_text",
            )
            config.character_directory = Path(temp) / "characters"
            config.character_directory.mkdir()
            Image.new("RGB", (32, 32)).save(config.character_directory / "A12.jpg")
            pipeline = Pipeline(config, RunControl(), lambda *_: None, lambda *_: None)
            pipeline.local = validate_local(config)
            _, name, path = reserve_sticker(config.output.directory)
            scene = Scene(pipeline, path, {"sticker": name}, "prompts")
            with patch("birthday_stickers.presets.select_scene", side_effect=RunHalted("paused")):
                with self.assertRaises(RunHalted), scene.guard():
                    scene.prepare(1, [])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("references", saved)
            self.assertNotIn("planning_prompt", saved)
            original_refs = copy.deepcopy(saved["references"])
            original_seed = saved["qwen_seed"]
            Image.new("RGB", (32, 32)).save(config.character_directory / "A13.jpg")
            with patch.object(LLMClient, "complete", return_value="Girl <image1> and baby dragon."):
                try:
                    Scene(pipeline, path, saved, "prompts").prepare(1, [])
                except KeyError as error:
                    self.fail(f"Partial scene must recover on resume, missing {error}")
            recovered = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["status"], "prompts_ready")
            self.assertEqual(recovered["references"], original_refs)
            self.assertEqual(recovered["qwen_seed"], original_seed)
            self.assertIn(recovered["planning_prompt"], recovered["planning_request"])

    def test_resume_restores_missing_brief_from_existing_selection(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = with_preset(
                fixtures.PipelineTests().configuration(temp, service), ROOT / "baby_dragon_text"
            )
            pipeline = Pipeline(config, RunControl(), lambda *_: None, lambda *_: None)
            pipeline.local = validate_local(config)
            _, name, path = reserve_sticker(config.output.directory)
            scene = Scene(pipeline, path, {"sticker": name}, "prompts")
            with patch.object(LLMClient, "complete", side_effect=RunHalted("paused")):
                with self.assertRaises(RunHalted), scene.guard():
                    scene.prepare(1, [])
            saved = json.loads(path.read_text(encoding="utf-8"))
            selection = copy.deepcopy(saved["scene_selection"])
            del saved["planning_prompt"]
            del saved["planning_request"]
            with patch(
                "birthday_stickers.presets.select_scene", side_effect=AssertionError("reroll")
            ):
                with patch.object(LLMClient, "complete", return_value="One baby dragon sleeps."):
                    try:
                        Scene(pipeline, path, saved, "prompts").prepare(1, [])
                    except KeyError as error:
                        self.fail(f"Saved selection must restore missing {error}")
            recovered = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["scene_selection"], selection)
            self.assertEqual(recovered["planning_prompt"], selection["prompt"])

    def test_activity_history_ignores_submitted_workflow_json_files(self):
        with tempfile.TemporaryDirectory() as temp:
            preset = load_preset(write_preset(Path(temp) / "preset"))
            other = copy.deepcopy(preset.activities[0])
            other["id"] = "other"
            preset.activities.append(other)
            metadata = Path(temp) / "metadata"
            metadata.mkdir()
            (metadata / "stiker-0000.json").write_text(
                json.dumps({"scene_selection": {"activity": {"id": preset.activities[0]["id"]}}}),
                encoding="utf-8",
            )
            for name in (
                "stiker-0000.qwen.json",
                "stiker-0000.flux.json",
                "stiker-not-a-scene.json",
            ):
                (metadata / name).write_text(
                    "Not scene metadata; must not be parsed", encoding="utf-8"
                )
            try:
                selected = select_scene(preset, metadata)
            except ValueError as error:
                self.fail(f"Workflow files must be ignored by activity sampling: {error}")
            self.assertEqual(selected["activity"]["id"], "other")
