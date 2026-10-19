import json
import unittest
from pathlib import Path

from birthday_stickers.images import plan_padding
from birthday_stickers.workflows import build_outpaint

ROOT = Path(__file__).resolve().parents[1]
FLUX_NODES = {
    "image": "200",
    "encoder": "112",
    "pad": "202",
    "sampler": "114",
    "output": "207",
}


class WorkflowTests(unittest.TestCase):
    def test_flux_uses_original_scene_and_actual_padding(self):
        source = json.loads(
            (ROOT / "src/birthday_stickers/flows/common/qwen-outpaint.json").read_text(
                encoding="utf-8"
            )
        )
        plan = plan_padding((1001, 777), (1500, 1000))
        result = build_outpaint(
            source,
            FLUX_NODES,
            {"114": {"steps": 25}},
            "RAW",
            "generated.jpg",
            plan,
            24,
            77,
            "stiker-0000",
        )
        self.assertEqual(result["112"]["inputs"]["prompt"], "RAW")
        self.assertEqual(result["200"]["inputs"]["image"], "generated.jpg")
        self.assertNotIn("17", result)
        self.assertEqual(result["202"]["inputs"]["left"], 251)
        self.assertEqual(result["202"]["inputs"]["right"], 252)
        self.assertEqual(result["114"]["inputs"]["latent_image"], ["112", 2])
        self.assertEqual(result["206"]["inputs"]["mask"], ["202", 1])
        self.assertEqual(result["206"]["inputs"]["destination"], ["202", 0])
        self.assertEqual(result["112"]["inputs"]["resolution"], 0)
        self.assertNotIn("noise_mask", result["114"]["inputs"])
        self.assertNotIn("201", result)
        self.assertEqual(source["200"]["inputs"]["image"], "")
        self.assertEqual(result["114"]["inputs"]["steps"], 25)

    def test_preserve_center_can_be_disabled_without_restoration(self):
        source = json.loads(
            (ROOT / "src/birthday_stickers/flows/common/qwen-outpaint.json").read_text(
                encoding="utf-8"
            )
        )
        graph = build_outpaint(
            source,
            FLUX_NODES,
            {},
            "RAW",
            "image.jpg",
            plan_padding((32, 32), (64, 64)),
            4,
            1,
            "test",
            preserve_center=False,
        )
        self.assertEqual(graph["207"]["inputs"]["images"], ["116", 0])
        self.assertNotIn("206", graph)

    def test_unknown_override_fails_instead_of_silently_ignoring_typo(self):
        source = json.loads(
            (ROOT / "src/birthday_stickers/flows/common/qwen-outpaint.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaisesRegex(ValueError, "stepz"):
            build_outpaint(
                source,
                FLUX_NODES,
                {"114": {"stepz": 25}},
                "RAW",
                "a.jpg",
                plan_padding((1024, 1024), (1536, 1536)),
                24,
                1,
                "x",
            )
