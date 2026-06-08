import json
import tempfile
import unittest
from pathlib import Path

from agent_ci_failure_correlator.config import CorrelatorConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_default_config_has_expected_threshold(self):
        config = CorrelatorConfig.default()
        self.assertGreater(config.similarity_threshold, 0.5)
        self.assertIn("the", config.stop_words)

    def test_from_mapping_overrides_known_fields(self):
        config = CorrelatorConfig.from_mapping({"similarity_threshold": 0.42, "min_cluster_size": "2"})
        self.assertEqual(config.similarity_threshold, 0.42)
        self.assertEqual(config.min_cluster_size, 2)

    def test_from_mapping_ignores_unknown_fields(self):
        config = CorrelatorConfig.from_mapping({"unknown": "value"})
        self.assertFalse(hasattr(config, "unknown"))

    def test_from_file_loads_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"fail_on_cross_repo": True}), encoding="utf-8")
            config = CorrelatorConfig.from_file(path)
        self.assertTrue(config.fail_on_cross_repo)

    def test_load_config_applies_overrides(self):
        config = load_config(overrides={"fail_on_any_failure": True})
        self.assertTrue(config.fail_on_any_failure)

    def test_to_dict_round_trip_custom_rules(self):
        original = CorrelatorConfig(custom_root_causes={"fixture": ["missing fixture"]})
        loaded = CorrelatorConfig.from_mapping(original.to_dict())
        self.assertEqual(loaded.custom_root_causes["fixture"], ["missing fixture"])


if __name__ == "__main__":
    unittest.main()
