import unittest

from agent_ci_failure_correlator.clusterer import cluster_failures, jaccard, label_similarity, similarity
from agent_ci_failure_correlator.config import CorrelatorConfig
from agent_ci_failure_correlator.models import FailureEvent, SourceRef


def event(event_id, repo, text, labels=None, command=""):
    labels = labels or ["python-import"]
    tokens = [token.strip("':").lower() for token in text.replace("@", "").replace("/", " ").split() if len(token) > 2]
    return FailureEvent(
        event_id=event_id,
        source=SourceRef(path=f"{event_id}.log", repository=repo, workflow="CI", job_name="test"),
        raw_text=text,
        summary=text,
        normalized_text=text.lower(),
        tokens=list(dict.fromkeys(tokens)),
        root_cause_labels=labels,
        rule_hits=labels if labels != ["unknown"] else [],
        command=command,
        language="python" if "python" in labels[0] else "unknown",
    )


class ClustererTests(unittest.TestCase):
    def test_jaccard_empty_sets(self):
        self.assertEqual(jaccard(set(), set()), 0.0)

    def test_jaccard_overlap(self):
        self.assertAlmostEqual(jaccard({"a", "b"}, {"b", "c"}), 1 / 3)

    def test_label_similarity_unknown_is_low(self):
        self.assertLess(label_similarity(["unknown"], ["unknown"]), 0.2)

    def test_similarity_higher_for_same_root_cause(self):
        left = event("a", "org/a", "ModuleNotFoundError no module named shared_auth", command="python -m pytest")
        right = event("b", "org/b", "ModuleNotFoundError no module named shared_auth", command="python -m pytest")
        self.assertGreater(similarity(left, right), 0.7)

    def test_similarity_lower_for_different_labels(self):
        left = event("a", "org/a", "ModuleNotFoundError no module named shared_auth", ["python-import"])
        right = event("b", "org/b", "npm err cannot find module ui-theme", ["javascript-dependency"])
        self.assertLess(similarity(left, right), 0.6)

    def test_cluster_failures_groups_repeated_failures(self):
        events = [
            event("a", "org/a", "ModuleNotFoundError no module named shared_auth"),
            event("b", "org/b", "ModuleNotFoundError no module named shared_auth"),
        ]
        clusters = cluster_failures(events, CorrelatorConfig(similarity_threshold=0.55))
        self.assertEqual(len(clusters), 1)
        self.assertTrue(clusters[0].is_cross_repository)

    def test_cluster_failures_splits_distinct_causes(self):
        events = [
            event("a", "org/a", "ModuleNotFoundError no module named shared_auth", ["python-import"]),
            event("b", "org/b", "npm err cannot find module ui-theme", ["javascript-dependency"]),
        ]
        clusters = cluster_failures(events, CorrelatorConfig(similarity_threshold=0.65))
        self.assertEqual(len(clusters), 2)

    def test_min_cluster_size_filters_singletons(self):
        events = [
            event("a", "org/a", "ModuleNotFoundError no module named shared_auth"),
            event("b", "org/b", "AssertionError assert 2 equals 3", ["test-assertion"]),
        ]
        clusters = cluster_failures(events, CorrelatorConfig(similarity_threshold=0.65, min_cluster_size=2))
        self.assertEqual(clusters, [])

    def test_cluster_contains_suggested_actions(self):
        events = [event("a", "org/a", "ModuleNotFoundError no module named shared_auth")]
        clusters = cluster_failures(events, CorrelatorConfig.default())
        self.assertIn("Python dependencies", clusters[0].suggested_actions[0])

    def test_cluster_ids_are_renumbered_after_sort(self):
        events = [
            event("a", "org/a", "AssertionError assert 2 == 3", ["test-assertion"]),
            event("b", "org/b", "ModuleNotFoundError no module named shared_auth", ["python-import"]),
            event("c", "org/c", "ModuleNotFoundError no module named shared_auth", ["python-import"]),
        ]
        clusters = cluster_failures(events, CorrelatorConfig(similarity_threshold=0.55))
        self.assertEqual(clusters[0].cluster_id, "C001")
        self.assertTrue(clusters[0].is_cross_repository)


if __name__ == "__main__":
    unittest.main()
