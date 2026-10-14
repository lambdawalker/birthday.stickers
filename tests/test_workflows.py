import copy
import json
import unittest
from pathlib import Path

from birthday_stickers.images import plan_padding
from birthday_stickers.workflows import build_flux, build_qwen

ROOT = Path(__file__).resolve().parents[1]
QWEN_NODES = {
    "protagonist": "514",
    "creature": "516",
    "encoder": "459:474",
    "latent": "459:456",
    "sampler": "459:458",
    "output": "461",
}
FLUX_NODES = {
    "image": "47",
    "prompt": "23",
    "pad": "44",
    "sampler": "3",
    "conditioning": "38",
    "output": "9",
}


class WorkflowTests(unittest.TestCase):
    def test_qwen_uses_refined_prompt_and_ordered_references_without_internal_rewriter(self):
        source = json.loads(
            (ROOT / "src/birthday_stickers/flows/multi_character_reference/qwen.json").read_text(
                encoding="utf-8"
            )
        )
        original = copy.deepcopy(source)
        result = build_qwen(
            source, QWEN_NODES, {}, "REFINED", "hero.png", "pet.png", 1024, 768, 42, "stiker-0000"
        )
        encoder = result["459:474"]["inputs"]
        self.assertEqual(encoder["prompt"], "REFINED")
        self.assertEqual(encoder["images.image_1"], ["514", 0])
        self.assertEqual(encoder["images.image_2"], ["516", 0])
        self.assertEqual(result["514"]["inputs"]["image"], "hero.png")
        self.assertEqual(result["516"]["inputs"]["image"], "pet.png")
        self.assertEqual(result["459:458"]["inputs"]["seed"], 42)
        self.assertEqual(result["459:458"]["inputs"]["latent_image"], ["459:456", 0])
        self.assertEqual(result["459:456"]["inputs"]["height"], 768)
        self.assertEqual(result["461"]["class_type"], "SaveImage")
        self.assertFalse({"459:477", "459:479", "459:480", "459:500", "502"} & result.keys())
        self.assertEqual(source, original)

    def test_flux_uses_original_scene_and_actual_padding(self):
        source = json.loads(
            (ROOT / "src/birthday_stickers/flows/common/flux-outpaint.json").read_text(
                encoding="utf-8"
            )
        )
        plan = plan_padding((1001, 777), (1500, 1000))
        result = build_flux(
            source,
            FLUX_NODES,
            {"3": {"steps": 25}},
            "RAW",
            "generated.jpg",
            plan,
            24,
            77,
            "stiker-0000",
        )
        self.assertEqual(result["23"]["inputs"]["text"], "RAW")
        self.assertEqual(result["47"]["inputs"]["image"], "generated.jpg")
        self.assertNotIn("17", result)
        self.assertEqual(result["44"]["inputs"]["left"], 251)
        self.assertEqual(result["44"]["inputs"]["right"], 252)
        self.assertTrue(result["38"]["inputs"]["noise_mask"])
        self.assertEqual(result["3"]["inputs"]["steps"], 25)

    def test_wrong_node_mapping_fails_clearly(self):
        source = json.loads(
            (ROOT / "src/birthday_stickers/flows/multi_character_reference/qwen.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaisesRegex(ValueError, "protagonist.*LoadImage"):
            build_qwen(
                source,
                {**QWEN_NODES, "protagonist": "502"},
                {},
                "prompt",
                "a.png",
                "b.png",
                1024,
                1024,
                1,
                "stiker-0000",
            )

    def test_unknown_override_fails_instead_of_silently_ignoring_typo(self):
        source = json.loads(
            (ROOT / "src/birthday_stickers/flows/common/flux-outpaint.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaisesRegex(ValueError, "stepz"):
            build_flux(
                source,
                FLUX_NODES,
                {"3": {"stepz": 25}},
                "RAW",
                "a.jpg",
                plan_padding((1024, 1024), (1536, 1536)),
                24,
                1,
                "x",
            )
