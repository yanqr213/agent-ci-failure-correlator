import json
import tempfile
import unittest
from pathlib import Path

from agent_ci_failure_correlator.api import analyze, analyze_paths, analyze_records
import agent_ci_failure_correlator.cli as cli_module
from agent_ci_failure_correlator.cli import (
    EXIT_CROSS_REPOSITORY_REPEATS,
    EXIT_FAILURES_FOUND,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    main,
)
from agent_ci_failure_correlator.config import CorrelatorConfig
from agent_ci_failure_correlator.models import FailureEvent, SourceRef
from agent_ci_failure_correlator.report import render_brief, render_json, render_markdown, render_sarif


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

    def test_render_sarif_contains_cluster_result(self):
        result = analyze([make_event("a", "org/a"), make_event("b", "org/b")], CorrelatorConfig(similarity_threshold=0.55))
        data = json.loads(render_sarif(result))
        run = data["runs"][0]

        self.assertEqual(data["version"], "2.1.0")
        self.assertEqual(run["tool"]["driver"]["name"], "agent-ci-failure-correlator")
        self.assertEqual(run["results"][0]["ruleId"], "ci-failure.python-import")
        self.assertEqual(run["results"][0]["level"], "error")
        self.assertTrue(run["results"][0]["properties"]["is_cross_repository"])

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

    def test_cli_analyze_writes_sarif_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.json"
            b = Path(tmp) / "b.json"
            output_path = Path(tmp) / "reports" / "report.sarif"
            a.write_text(json.dumps({"repository": "org/a", "log": "ModuleNotFoundError: No module named shared_auth"}), encoding="utf-8")
            b.write_text(json.dumps({"repository": "org/b", "log": "ModuleNotFoundError: No module named shared_auth"}), encoding="utf-8")
            code = main(["analyze", str(a), str(b), "--similarity-threshold", "0.55", "--format", "sarif", "--output", str(output_path)])
            self.assertEqual(code, EXIT_SUCCESS)
            data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(data["runs"][0]["tool"]["driver"]["name"], "agent-ci-failure-correlator")

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

    def test_cli_fetch_github_writes_analyzable_jsonl(self):
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def list_workflow_runs(self, repository, *, page, per_page, branch="", created=""):
                if page > 1:
                    return []
                return [
                    {
                        "id": 9,
                        "name": "CI",
                        "conclusion": "failure",
                        "html_url": f"https://github.com/{repository}/actions/runs/9",
                    }
                ]

            def list_run_jobs(self, repository, run_id):
                return [{"id": 99, "name": "tests", "conclusion": "failure"}]

            def download_job_log(self, repository, job_id):
                return "ModuleNotFoundError: No module named shared_auth"

        original = cli_module._github_client_factory
        cli_module._github_client_factory = FakeClient
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "failures.jsonl"
                code = main(["fetch-github", "org/api", "--output", str(output), "--limit", "5"])
                self.assertEqual(code, EXIT_SUCCESS)
                result = analyze_paths([str(output)], config=CorrelatorConfig.default())
                self.assertEqual(1, len(result.events))
                self.assertEqual("org/api", result.events[0].source.repository)
                self.assertIn("python-import", result.events[0].root_cause_labels)
        finally:
            cli_module._github_client_factory = original

    def test_cli_fetch_github_requires_repository_or_owner(self):
        code = main(["fetch-github", "--no-logs"])
        self.assertEqual(code, EXIT_USAGE_ERROR)

    def test_cli_fetch_github_discovers_owner_repositories(self):
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.owner_calls = []

            def list_owner_repositories(self, owner, *, page, per_page, repo_type="all"):
                self.owner_calls.append((owner, page, per_page, repo_type))
                if page > 1:
                    return []
                return [
                    {"full_name": f"{owner}/api", "archived": False, "fork": False},
                    {"full_name": f"{owner}/docs", "archived": False, "fork": False},
                    {"full_name": f"{owner}/old", "archived": True, "fork": False},
                ]

            def list_workflow_runs(self, repository, *, page, per_page, branch="", created=""):
                if page > 1:
                    return []
                return [
                    {
                        "id": f"{repository}-9",
                        "name": "CI",
                        "conclusion": "failure",
                        "html_url": f"https://github.com/{repository}/actions/runs/9",
                    }
                ]

            def list_run_jobs(self, repository, run_id):
                return [{"id": f"{repository}-99", "name": "tests", "conclusion": "failure"}]

            def download_job_log(self, repository, job_id):
                return f"ModuleNotFoundError: No module named shared_{repository.split('/')[-1]}"

        original = cli_module._github_client_factory
        cli_module._github_client_factory = FakeClient
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "failures.jsonl"
                code = main(
                    [
                        "fetch-github",
                        "--owner",
                        "org",
                        "--repo-name-pattern",
                        "/api$",
                        "--output",
                        str(output),
                        "--limit",
                        "5",
                    ]
                )
                self.assertEqual(code, EXIT_SUCCESS)
                rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(["org/api"], [row["repository"] for row in rows])
                result = analyze_paths([str(output)], config=CorrelatorConfig.default())
                self.assertEqual("org/api", result.events[0].source.repository)
        finally:
            cli_module._github_client_factory = original

    def test_cli_fetch_github_combines_owner_and_explicit_repositories(self):
        class FakeClient:
            def __init__(self, **kwargs):
                self.requested_repositories = []
                pass

            def list_owner_repositories(self, owner, *, page, per_page, repo_type="all"):
                if page > 1:
                    return []
                return [{"full_name": f"{owner}/api", "archived": False, "fork": False}]

            def list_workflow_runs(self, repository, *, page, per_page, branch="", created=""):
                self.requested_repositories.append(repository)
                if page > 1:
                    return []
                return [{"id": f"{repository}-9", "name": "CI", "conclusion": "failure"}]

            def list_run_jobs(self, repository, run_id):
                return [{"id": f"{repository}-99", "name": "tests", "conclusion": "failure"}]

            def download_job_log(self, repository, job_id):
                return "ModuleNotFoundError: No module named shared_auth"

        original = cli_module._github_client_factory
        fake_client = None

        def factory(**kwargs):
            nonlocal fake_client
            fake_client = FakeClient(**kwargs)
            return fake_client

        cli_module._github_client_factory = factory
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "failures.jsonl"
                code = main(["fetch-github", "extra/repo", "org/api", "--owner", "org", "--output", str(output)])
                self.assertEqual(code, EXIT_SUCCESS)
                rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
                self.assertEqual({"org/api", "extra/repo"}, {row["repository"] for row in rows})
                self.assertEqual(["org/api", "extra/repo"], fake_client.requested_repositories)
        finally:
            cli_module._github_client_factory = original

    def test_cli_audit_github_writes_current_status_json(self):
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def get_repository(self, repository):
                return {"default_branch": "main"}

            def list_current_workflow_runs(
                self,
                repository,
                *,
                page,
                per_page,
                branch="",
                head_sha="",
                exclude_pull_requests=False,
            ):
                if head_sha == "badsha":
                    return [
                        {
                            "id": 99,
                            "workflow_id": 1,
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "failure",
                            "head_branch": "feature",
                            "head_sha": "badsha",
                            "html_url": "https://github.com/org/api/actions/runs/99",
                        }
                    ]
                return [
                    {
                        "id": 1,
                        "workflow_id": 1,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "head_branch": "main",
                        "head_sha": "goodsha",
                        "html_url": "https://github.com/org/api/actions/runs/1",
                    }
                ]

            def list_pull_requests(self, repository, *, page, per_page, state="open"):
                return [
                    {
                        "number": 3,
                        "title": "Failing change",
                        "html_url": "https://github.com/org/api/pull/3",
                        "head": {"ref": "feature", "sha": "badsha"},
                        "base": {"ref": "main"},
                    }
                ]

        original = cli_module._github_client_factory
        cli_module._github_client_factory = FakeClient
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "current.json"
                code = main(["audit-github", "org/api", "--format", "json", "--output", str(output)])
                self.assertEqual(code, EXIT_SUCCESS)
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual("agent-ci-failure-correlator.current-actions.v1", payload["schema"])
                self.assertEqual(1, payload["summary"]["problem_count"])
                self.assertEqual("open-pr:3", payload["problems"][0]["scope"])
        finally:
            cli_module._github_client_factory = original

    def test_cli_audit_github_fail_on_current_problem(self):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def get_repository(self, repository):
                return {"default_branch": "main"}

            def list_current_workflow_runs(
                self,
                repository,
                *,
                page,
                per_page,
                branch="",
                head_sha="",
                exclude_pull_requests=False,
            ):
                return [
                    {
                        "id": 9,
                        "workflow_id": 1,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_branch": "main",
                        "head_sha": "badsha",
                    }
                ]

            def list_pull_requests(self, repository, *, page, per_page, state="open"):
                return []

        original = cli_module._github_client_factory
        cli_module._github_client_factory = FakeClient
        try:
            code = main(["audit-github", "org/api", "--no-open-prs", "--fail-on-current-problem"])
            self.assertEqual(code, EXIT_FAILURES_FOUND)
        finally:
            cli_module._github_client_factory = original

    def test_cli_audit_github_discovers_owner_repositories(self):
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def list_owner_repositories(self, owner, *, page, per_page, repo_type="all"):
                if page > 1:
                    return []
                return [{"full_name": f"{owner}/api", "archived": False, "fork": False}]

            def get_repository(self, repository):
                return {"default_branch": "main"}

            def list_current_workflow_runs(
                self,
                repository,
                *,
                page,
                per_page,
                branch="",
                head_sha="",
                exclude_pull_requests=False,
            ):
                return [
                    {
                        "id": 1,
                        "workflow_id": 1,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "head_branch": branch or "main",
                        "head_sha": "goodsha",
                    }
                ]

            def list_pull_requests(self, repository, *, page, per_page, state="open"):
                return []

        original = cli_module._github_client_factory
        cli_module._github_client_factory = FakeClient
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "current.md"
                code = main(["audit-github", "--owner", "org", "--output", str(output)])
                self.assertEqual(code, EXIT_SUCCESS)
                text = output.read_text(encoding="utf-8")
                self.assertIn("CLEAR", text)
                self.assertIn("Repositories scanned: 1", text)
        finally:
            cli_module._github_client_factory = original


if __name__ == "__main__":
    unittest.main()
