import json
import tempfile
import unittest
from pathlib import Path

from birthday_stickers.activities import load_activities, select_activity
from birthday_stickers.pipeline import planning_prompt

ROOT = Path(__file__).resolve().parents[1]


class ActivityTests(unittest.TestCase):
    def test_catalog_and_persistent_cycles(self):
        catalog = load_activities(ROOT / "prompts/activities.json")
        self.assertEqual(len(catalog), 300)
        self.assertEqual(len({x["activity"] for x in catalog}), 300)
        with tempfile.TemporaryDirectory() as temp:
            history = Path(temp)
            seen = set()
            previous = None
            for i in range(301):
                selected = select_activity(catalog, history)
                if i < 300:
                    self.assertNotIn(selected["id"], seen)
                    seen.add(selected["id"])
                else:
                    self.assertEqual(selected["cycle"], 2)
                if i < 275:
                    self.assertNotEqual(selected["category"], previous)
                previous = selected["category"]
                (history / f"stiker-{i:04}.json").write_text(
                    json.dumps({"activity": selected}), encoding="utf-8"
                )

    def test_invalid_catalog_and_corrupt_history_fail_clearly(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.json"
            path.write_text("[{}]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "activity"):
                load_activities(path)
            path.unlink()
            (Path(temp) / "stiker-0000.json").write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "history"):
                select_activity(load_activities(ROOT / "prompts/activities.json"), Path(temp))

    def test_brief_is_injected_even_with_legacy_template(self):
        brief = {"activity": "Read together.", "setting": "longhouse"}
        text = planning_prompt("Scene {{iteration}}", 1, 2, "stiker-0000", [], brief)
        self.assertIn("Read together.", text)
        self.assertIn("longhouse", text)
        self.assertIn("Scene 1", text)
