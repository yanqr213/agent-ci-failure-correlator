import unittest

from agent_ci_failure_correlator.config import CorrelatorConfig
from agent_ci_failure_correlator.normalizer import (
    canonical_repository,
    normalize_text,
    stable_event_id,
    summarize_and_normalize,
    summarize_log,
    tokenize,
)


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        self.config = CorrelatorConfig.default()

    def test_summarize_log_extracts_error_context(self):
        log = "\n".join(["line 1", "before", "ERROR failed here", "after", "tail"])
        summary = summarize_log(log, self.config)
        self.assertIn("ERROR failed here", summary)
        self.assertIn("before", summary)

    def test_summarize_log_uses_first_lines_without_error(self):
        summary = summarize_log("one\ntwo\nthree", CorrelatorConfig(max_summary_lines=2))
        self.assertEqual(summary, "one\ntwo")

    def test_summarize_log_ignores_configured_noise(self):
        summary = summarize_log("Run python -m pytest\nshell: bash\nERROR broken", self.config)
        self.assertNotIn("shell:", summary)
        self.assertIn("ERROR broken", summary)

    def test_normalize_text_replaces_url_and_hash(self):
        text = normalize_text("See http://127.0.0.1/a at abcdef1234567890")
        self.assertIn("<url>", text)
        self.assertIn("<hash>", text)

    def test_normalize_text_replaces_paths(self):
        text = normalize_text(r"File C:\runner\repo\tests\test_a.py line 42")
        self.assertIn("<path>", text)

    def test_tokenize_dedupes_and_removes_stop_words(self):
        tokens = tokenize("the module module missing shared_auth", ["the"])
        self.assertEqual(tokens.count("module"), 1)
        self.assertIn("shared_auth", tokens)

    def test_stable_event_id_is_stable(self):
        self.assertEqual(stable_event_id(["a", "b"]), stable_event_id(["a", "b"]))
        self.assertNotEqual(stable_event_id(["a", "b"]), stable_event_id(["b", "a"]))

    def test_canonical_repository_uses_alias(self):
        repo = canonical_repository("api", {"api": "org/api-service"})
        self.assertEqual(repo, "org/api-service")

    def test_summarize_and_normalize_returns_tokens(self):
        summary, normalized, tokens = summarize_and_normalize("ModuleNotFoundError: No module named shared_auth", self.config)
        self.assertIn("modulenotfounderror", normalized)
        self.assertIn("shared_auth", tokens)
        self.assertIn("ModuleNotFoundError", summary)


if __name__ == "__main__":
    unittest.main()
