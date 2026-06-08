import unittest

from agent_ci_failure_correlator.models import AnalysisResult, FailureEvent, RootCauseCluster, SourceRef


def make_event(event_id="e1", repo="org/a"):
    return FailureEvent(
        event_id=event_id,
        source=SourceRef(path="input.log", format="log", repository=repo, workflow="CI"),
        raw_text="ModuleNotFoundError: No module named shared_auth",
        summary="ModuleNotFoundError: No module named shared_auth",
        normalized_text="modulenotfounderror no module named shared_auth",
        tokens=["modulenotfounderror", "module", "named", "shared_auth"],
        root_cause_labels=["python-import"],
        rule_hits=["python-import"],
    )


class ModelTests(unittest.TestCase):
    def test_source_ref_from_mapping_accepts_aliases(self):
        source = SourceRef.from_mapping({"repo": "org/a", "job": "tests", "runId": 12})
        self.assertEqual(source.repository, "org/a")
        self.assertEqual(source.job_name, "tests")
        self.assertEqual(source.run_id, "12")

    def test_failure_event_round_trip(self):
        event = make_event()
        loaded = FailureEvent.from_dict(event.to_dict())
        self.assertEqual(loaded.event_id, event.event_id)
        self.assertEqual(loaded.source.repository, "org/a")

    def test_cluster_properties_cross_repo(self):
        cluster = RootCauseCluster(
            cluster_id="C001",
            root_cause_labels=["python-import"],
            confidence=0.8,
            severity="error",
            representative_summary="summary",
            normalized_signature="sig",
            events=[make_event("a", "org/a"), make_event("b", "org/b")],
            repositories=["org/a", "org/b"],
            suggested_actions=["fix"],
        )
        self.assertEqual(cluster.event_count, 2)
        self.assertTrue(cluster.is_cross_repository)

    def test_analysis_result_summary(self):
        cluster = RootCauseCluster(
            cluster_id="C001",
            root_cause_labels=["python-import"],
            confidence=0.8,
            severity="error",
            representative_summary="summary",
            normalized_signature="sig",
            events=[make_event("a", "org/a"), make_event("b", "org/b")],
            repositories=["org/a", "org/b"],
            suggested_actions=["fix"],
        )
        result = AnalysisResult(events=cluster.events, clusters=[cluster], warnings=[], inputs=["x"], config={})
        summary = result.to_dict()["summary"]
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["cross_repository_cluster_count"], 1)


if __name__ == "__main__":
    unittest.main()
