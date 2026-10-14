import tempfile
import unittest
from pathlib import Path

from birthday_stickers.references import discover_characters


class ReferenceTests(unittest.TestCase):
    def test_groups_all_character_ids_and_ignores_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in (
                "A000.jpg",
                "A001.jpg",
                "B012.jpg",
                "Z999.JPG",
                "A12.jpg",
                "a000.jpg",
                "AA000.jpg",
                "A.jpg",
                "A001.png",
                "README.md",
            ):
                (directory / name).touch()
            (directory / "C000.jpg").mkdir()
            groups = discover_characters(directory)
            self.assertEqual(set(groups), {"A", "B", "Z"})
            self.assertEqual([p.name for p in groups["A"]], ["A000.jpg", "A001.jpg", "A12.jpg"])
            self.assertEqual([p.name for p in groups["Z"]], ["Z999.JPG"])

    def test_missing_directory_reports_path(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "directory"):
                discover_characters(Path(temp) / "missing")
