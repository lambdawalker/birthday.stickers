import json
import unittest
from pathlib import Path

from birthday_stickers.images import plan_padding
from birthday_stickers.workflows import build_flux

ROOT = Path(__file__).resolve().parents[1]
FLUX_NODES = {
    "image": "47",
    "prompt": "23",
    "pad": "44",
    "sampler": "3",
    "conditioning": "38",
    "output": "9",
}


class WorkflowTests(unittest.TestCase):
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
