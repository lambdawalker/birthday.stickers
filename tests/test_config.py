import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from birthday_stickers.config import load_config
from birthday_stickers.flows import expand_config

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def load_modified(self, mutate):
        data = expand_config(
            yaml.safe_load((ROOT / "tests/fixtures/config.yaml").read_text(encoding="utf-8")), ROOT
        )
        mutate(data)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.yaml"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            return load_config(path)

    def test_paths_are_relative_to_yaml(self):
        config = load_config(ROOT / "tests/fixtures/config.yaml")
        self.assertEqual(config.character_directory, ROOT / "assets/characters")
        self.assertEqual(config.protagonist, "A")
        self.assertEqual(config.creature, "B")
        self.assertEqual(config.output.directory, ROOT / "output")

    def test_invalid_configs_fail_before_network_requests(self):
        cases = [
            (lambda d: d["characters"].update(protagonist="AA"), "A-Z"),
            (lambda d: d["characters"].update(creature="a"), "A-Z"),
            (lambda d: d["characters"].update(creature="A"), "different character IDs"),
            (lambda d: d["generation"].update(count=0), "count"),
            (lambda d: d["generation"].update(count=True), "count"),
            (lambda d: d["generation"].update(width=1001), "multiple of 32"),
            (lambda d: d["generation"].update(width=48), "multiple of 32"),
            (lambda d: d["output"].update(jpeg_quality=101), "jpeg_quality"),
            (lambda d: d["llm"].update(base_url="ftp://server"), "http"),
            (lambda d: d["comfy"].update(poll_interval=0), "poll_interval"),
            (lambda d: d["comfy"].update(job_timeout=float("nan")), "job_timeout"),
            (lambda d: d["output"].update(width=10), "smaller"),
            (lambda d: d["generation"].update(coount=3), "coount"),
            (lambda d: d["llm"].update(extra_body={"stream": True}), "extra_body"),
        ]
        for mutate, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.load_modified(mutate)

    def test_loading_does_not_mutate_yaml_content(self):
        path = ROOT / "tests/fixtures/config.yaml"
        original = copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))
        load_config(path)
        self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8")), original)
