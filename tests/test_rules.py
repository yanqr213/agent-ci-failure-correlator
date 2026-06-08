import unittest

from agent_ci_failure_correlator.config import CorrelatorConfig
from agent_ci_failure_correlator.rules import (
    detect_root_causes,
    infer_language,
    severity_from_labels,
    suggested_actions,
)


class RuleTests(unittest.TestCase):
    def test_detect_python_import(self):
        labels, hits = detect_root_causes("modulenotfounderror no module named shared_auth", [], CorrelatorConfig.default())
        self.assertIn("python-import", labels)
        self.assertIn("python-import", hits)

    def test_detect_javascript_dependency(self):
        labels, _hits = detect_root_causes("npm err cannot find module @org/ui-theme", [], CorrelatorConfig.default())
        self.assertIn("javascript-dependency", labels)

    def test_detect_timeout(self):
        labels, _hits = detect_root_causes("job timed out after 360 minutes", [], CorrelatorConfig.default())
        self.assertIn("timeout", labels)

    def test_detect_auth_permission(self):
        labels, _hits = detect_root_causes("Resource not accessible by integration", [], CorrelatorConfig.default())
        self.assertIn("auth-permission", labels)

    def test_detect_custom_root_cause(self):
        config = CorrelatorConfig(custom_root_causes={"fixture": ["golden fixture .* missing"]})
        labels, hits = detect_root_causes("golden fixture user.json missing", [], config)
        self.assertIn("fixture", labels)
        self.assertIn("custom:fixture", hits)

    def test_unknown_when_no_rule_matches(self):
        labels, hits = detect_root_causes("plain text without a clear root cause", [], CorrelatorConfig.default())
        self.assertEqual(labels, ["unknown"])
        self.assertEqual(hits, [])

    def test_infer_language_python(self):
        self.assertEqual(infer_language("pytest modulenotfounderror", ["python-import"]), "python")

    def test_infer_language_javascript(self):
        self.assertEqual(infer_language("npm err cannot find module", ["javascript-dependency"]), "javascript")

    def test_severity_critical_for_network(self):
        self.assertEqual(severity_from_labels(["network"], "econnreset"), "critical")

    def test_suggested_actions_uses_custom_override(self):
        config = CorrelatorConfig(label_actions={"python-import": ["Install shared package."]})
        actions = suggested_actions(["python-import"], config)
        self.assertEqual(actions, ["Install shared package."])


if __name__ == "__main__":
    unittest.main()
