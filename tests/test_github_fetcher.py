import json
import unittest

from agent_ci_failure_correlator.github_fetcher import (
    GitHubFetchOptions,
    fetch_failed_jobs,
    read_repositories,
    redact_log,
    render_jsonl,
)


class FakeGitHubClient:
    def __init__(self):
        self.downloaded = []

    def list_workflow_runs(self, repository, *, page, per_page, branch="", created=""):
        self.last_branch = branch
        self.last_created = created
        if page > 1:
            return []
        return [
            {
                "id": 101,
                "name": "CI",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "abc123",
                "html_url": f"https://github.com/{repository}/actions/runs/101",
                "run_number": 7,
                "run_attempt": 1,
                "event": "push",
                "created_at": "2026-06-08T10:00:00Z",
                "updated_at": "2026-06-08T10:05:00Z",
            },
            {
                "id": 102,
                "name": "CI",
                "conclusion": "success",
            },
        ]

    def list_run_jobs(self, repository, run_id):
        return [
            {"id": 1, "name": "lint", "conclusion": "success"},
            {
                "id": 2,
                "name": "tests",
                "status": "completed",
                "conclusion": "failure",
                "html_url": f"https://github.com/{repository}/actions/runs/{run_id}/job/2",
                "started_at": "2026-06-08T10:01:00Z",
                "completed_at": "2026-06-08T10:05:00Z",
                "steps": [
                    {"name": "checkout", "conclusion": "success", "number": 1},
                    {"name": "pytest", "conclusion": "failure", "number": 2},
                ],
            },
        ]

    def download_job_log(self, repository, job_id):
        self.downloaded.append((repository, job_id))
        return "Traceback\nModuleNotFoundError: No module named shared_auth\n" + "password" + "=super-secret"


class GitHubFetcherTests(unittest.TestCase):
    def test_fetch_failed_jobs_returns_redacted_job_records(self):
        client = FakeGitHubClient()
        result = fetch_failed_jobs(
            ["org/api"],
            GitHubFetchOptions(per_repo_limit=5, branch="main", days=7),
            client=client,
        )

        self.assertEqual([], result.warnings)
        self.assertEqual(1, len(result.records))
        record = result.records[0]
        self.assertEqual("org/api", record["repository"])
        self.assertEqual("tests", record["job_name"])
        self.assertEqual("failure", record["conclusion"])
        self.assertIn("ModuleNotFoundError", record["log"])
        self.assertNotIn("super-secret", record["log"])
        self.assertIn(("org/api", "2"), client.downloaded)
        self.assertEqual("main", client.last_branch)
        self.assertTrue(client.last_created.startswith(">="))

    def test_render_jsonl_is_parseable(self):
        rendered = render_jsonl([{"repository": "org/api", "log": "error"}])
        rows = [json.loads(line) for line in rendered.splitlines()]
        self.assertEqual("org/api", rows[0]["repository"])

    def test_read_repositories_deduplicates_and_validates(self):
        repos = read_repositories(["org/api", "org/api", "Org/Web"])
        self.assertEqual(["org/api", "Org/Web"], repos)
        with self.assertRaises(ValueError):
            read_repositories(["not-a-repo"])

    def test_redact_log_handles_common_secret_shapes(self):
        text = redact_log("Authorization: Bearer abcdef\napi_key = 123456\n" + "sk-" + "testsecret123456789012345")
        self.assertNotIn("abcdef", text)
        self.assertNotIn("123456", text)
        self.assertIn("[REDACTED]", text)


if __name__ == "__main__":
    unittest.main()
