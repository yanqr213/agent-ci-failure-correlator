import json
import tempfile
import unittest
from pathlib import Path

from agent_ci_failure_correlator.api import analyze, analyze_paths, analyze_records
from agent_ci_failure_correlator.cli import (
    EXIT_CROSS_REPOSITORY_REPEATS,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    main,
)
from agent_ci_failure_correlator.config import CorrelatorConfig
from agent_ci_failure_correlator.models import FailureEvent, SourceRef
from agent_ci_failure_correlator.report import render_brief, render_json, render_markdown


def make_event(event_id="e1", repo="org/a"):
    return FailureEvent(
        event_id=event_id,
        source=SourceRef(path="input.log", repository=repo, workflow="CI", job_name="tests"),
        raw_text="ModuleNotFoundError: No module named shared_auth",
        summary="ModuleNotFoundError: No module named shared_auth",
        normalized_text="modulenotfounderror no module named shared_auth",
        tokens=["modulenotfounderror", "module", "named", "shared_auth"],
        root_cause_labels=["python-import"],
        rule_hits=["python-import"],
        language="python",
    )


class ReportApiCliTests(unittest.TestCase):
    def test_analyze_clusters_events(self):
        result = analyze([make_event("a", "org/a"), make_event("b", "org/b")], CorrelatorConfig(similarity_threshold=0.55))
        self.assertEqual(len(result.clusters), 1)
        self.assertTrue(result.has_cross_repository_repeats)

    def test_analyze_records_normalizes(self):
        result = analyze_records(
            [{"repository": "org/a", "log": "ModuleNotFoundError: No module named shared_auth"}],
            CorrelatorConfig.default(),
        )
        self.assertEqual(result.events[0].source.repository, "org/a")

    def test_analyze_paths_examples(self):
        root = Path(__file__).resolve().parents[1]
        result = analyze_paths([str(root / "examples" / "inputs")], config=CorrelatorConfig(similarity_threshold=0.56))
        self.assertGreaterEqual(len(result.events), 7)
        self.assertTrue(result.has_cross_repository_repeats)
        python_clusters = [cluster for cluster in result.clusters if "python-import" in cluster.root_cause_labels]
        self.assertTrue(any("acme/checkout-service" in cluster.repositories for cluster in python_clusters))

    def test_render_json_is_valid_json(self):
        result = analyze([make_event()], CorrelatorConfig.default())
        data = json.loads(render_json(result))
        self.assertIn("summary", data)

    def test_render_markdown_contains_cluster_heading(self):
        result = analyze([make_event()], CorrelatorConfig.default())
        markdown = render_markdown(result)
        self.assertIn("# CI Failure Correlation Report", markdown)
        self.assertIn("## Clusters", markdown)

    def test_render_brief_prioritizes_cross_repo_repeats(self):
        result = analyze([make_event("a", "org/a"), make_event("b", "org/b")], CorrelatorConfig(similarity_threshold=0.55))
        brief = render_brief(result)

        self.assertIn("# CI Failure Triage Brief", brief)
        self.assertIn("Decision: BATCH-FIX", brief)
        self.assertIn("Top repeated causes:", brief)
        self.assertIn("python-import", brief)
        self.assertIn("Agent handoff:", brief)

    def test_cli_version_subcommand(self):
        self.assertEqual(main(["version"]), EXIT_SUCCESS)

    def test_cli_init_config_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            code = main(["init-config", "--output", str(path)])
            self.assertEqual(code, EXIT_SUCCESS)
            self.assertTrue(path.exists())

    def test_cli_init_config_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{}", encoding="utf-8")
            code = main(["init-config", "--output", str(path)])
            self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_cli_analyze_writes_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "failure.log"
            output_path = Path(tmp) / "report.json"
            input_path.write_text("ModuleNotFoundError: No module named shared_auth", encoding="utf-8")
            code = main(["analyze", str(input_path), "--format", "json", "--output", str(output_path)])
            self.assertEqual(code, EXIT_SUCCESS)
            data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["event_count"], 1)

    def test_cli_analyze_writes_brief_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.json"
            b = Path(tmp) / "b.json"
            output_path = Path(tmp) / "reports" / "brief.md"
            a.write_text(json.dumps({"repository": "org/a", "log": "ModuleNotFoundError: No module named shared_auth"}), encoding="utf-8")
            b.write_text(json.dumps({"repository": "org/b", "log": "ModuleNotFoundError: No module named shared_auth"}), encoding="utf-8")
            code = main(["analyze", str(a), str(b), "--similarity-threshold", "0.55", "--format", "brief", "--output", str(output_path)])
            self.assertEqual(code, EXIT_SUCCESS)
            text = output_path.read_text(encoding="utf-8")
            self.assertIn("Decision: BATCH-FIX", text)
            self.assertIn("python-import", text)

    def test_cli_analyze_creates_output_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "failure.log"
            output_path = root / "nested" / "reports" / "report.json"
            input_path.write_text("ModuleNotFoundError: No module named shared_auth", encoding="utf-8")
            code = main(["analyze", str(input_path), "--format", "json", "--output", str(output_path)])
            self.assertEqual(code, EXIT_SUCCESS)
            self.assertTrue(output_path.exists())

    def test_cli_analyze_cross_repo_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.json"
            b = Path(tmp) / "b.json"
            a.write_text(json.dumps({"repository": "org/a", "log": "ModuleNotFoundError: No module named shared_auth"}), encoding="utf-8")
            b.write_text(json.dumps({"repository": "org/b", "log": "ModuleNotFoundError: No module named shared_auth"}), encoding="utf-8")
            code = main(["analyze", str(a), str(b), "--similarity-threshold", "0.55", "--fail-on-cross-repo"])
        self.assertEqual(code, EXIT_CROSS_REPOSITORY_REPEATS)

    def test_cli_bad_config_returns_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "bad.json"
            input_path = Path(tmp) / "failure.log"
            config.write_text("{bad", encoding="utf-8")
            input_path.write_text("error", encoding="utf-8")
            code = main(["analyze", str(input_path), "--config", str(config)])
        self.assertEqual(code, EXIT_USAGE_ERROR)


if __name__ == "__main__":
    unittest.main()
