import json
import unittest

from agent_ci_failure_correlator.github_audit import (
    GitHubCurrentAuditOptions,
    audit_current_actions,
    audit_current_actions_for_owners,
    render_current_audit_json,
    render_current_audit_markdown,
)
from agent_ci_failure_correlator.github_fetcher import GitHubRepositoryDiscoveryOptions


class FakeAuditClient:
    def __init__(self):
        self.run_calls = []

    def get_repository(self, repository):
        return {"default_branch": "main" if repository != "org/lib" else "trunk"}

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
        self.run_calls.append((repository, branch, head_sha, exclude_pull_requests))
        if repository == "org/api" and branch == "main":
            return [
                self._run(101, "CI", "completed", "success", "main", "aaa111"),
                self._run(99, "CI", "completed", "failure", "main", "oldbad"),
            ]
        if repository == "org/api" and head_sha == "prbad":
            return [self._run(201, "CI", "completed", "failure", "feature", "prbad")]
        if repository == "org/api" and head_sha == "prpending":
            return [self._run(202, "Deploy", "in_progress", "", "feature-2", "prpending")]
        if repository == "org/lib" and branch == "trunk":
            return [self._run(301, "CI", "completed", "neutral", "trunk", "bbb222")]
        return []

    def list_pull_requests(self, repository, *, page, per_page, state="open"):
        if repository == "org/api" and page == 1:
            return [
                {
                    "number": 5,
                    "title": "Break API",
                    "html_url": "https://github.com/org/api/pull/5",
                    "head": {"ref": "feature", "sha": "prbad"},
                    "base": {"ref": "main"},
                },
                {
                    "number": 6,
                    "title": "Pending deploy",
                    "html_url": "https://github.com/org/api/pull/6",
                    "head": {"ref": "feature-2", "sha": "prpending"},
                    "base": {"ref": "main"},
                },
            ]
        return []

    def list_owner_repositories(self, owner, *, page, per_page, repo_type="all"):
        if page > 1:
            return []
        return [
            {"full_name": f"{owner}/api", "archived": False, "fork": False},
            {"full_name": f"{owner}/lib", "archived": False, "fork": False},
        ]

    @staticmethod
    def _run(run_id, name, status, conclusion, branch, sha):
        return {
            "id": run_id,
            "workflow_id": 10 if name == "CI" else 20,
            "name": name,
            "status": status,
            "conclusion": conclusion,
            "html_url": f"https://github.com/org/repo/actions/runs/{run_id}",
            "created_at": "2026-06-09T00:00:00Z",
            "updated_at": "2026-06-09T00:01:00Z",
            "head_branch": branch,
            "head_sha": sha,
            "event": "push",
            "run_number": run_id,
        }


class GitHubAuditTests(unittest.TestCase):
    def test_audit_current_actions_reports_open_pr_problems(self):
        result = audit_current_actions(["org/api"], client=FakeAuditClient())

        self.assertEqual(1, len(result.repositories))
        self.assertEqual(2, len(result.problem_heads))
        scopes = {head.scope for head in result.problem_heads}
        self.assertEqual({"open-pr:5", "open-pr:6"}, scopes)
        self.assertFalse(any(head.scope.startswith("default") for head in result.problem_heads))

    def test_audit_can_ignore_pending_heads(self):
        result = audit_current_actions(
            ["org/api"],
            GitHubCurrentAuditOptions(include_pending=False),
            client=FakeAuditClient(),
        )

        self.assertEqual(["open-pr:5"], [head.scope for head in result.problem_heads])

    def test_audit_can_skip_open_prs(self):
        result = audit_current_actions(
            ["org/api"],
            GitHubCurrentAuditOptions(include_open_prs=False),
            client=FakeAuditClient(),
        )

        self.assertEqual([], result.problem_heads)
        self.assertEqual([], result.repositories[0].open_pull_requests)

    def test_render_current_audit_json_schema(self):
        result = audit_current_actions(["org/api"], client=FakeAuditClient())
        payload = json.loads(render_current_audit_json(result))

        self.assertEqual("agent-ci-failure-correlator.current-actions.v1", payload["schema"])
        self.assertEqual(2, payload["summary"]["problem_count"])
        self.assertEqual(2, len(payload["problems"]))

    def test_render_current_audit_markdown_mentions_current_problems(self):
        result = audit_current_actions(["org/api"], client=FakeAuditClient())
        markdown = render_current_audit_markdown(result)

        self.assertIn("# Current GitHub Actions Audit", markdown)
        self.assertIn("ACTION NEEDED", markdown)
        self.assertIn("open-pr:5", markdown)
        self.assertIn("open-pr:6", markdown)

    def test_owner_audit_discovers_repositories(self):
        result = audit_current_actions_for_owners(
            ["org"],
            discovery_options=GitHubRepositoryDiscoveryOptions(per_owner_limit=2),
            client=FakeAuditClient(),
        )

        self.assertEqual(["org/api", "org/lib"], result.discovered.repositories)
        self.assertEqual(2, len(result.repositories))


if __name__ == "__main__":
    unittest.main()
