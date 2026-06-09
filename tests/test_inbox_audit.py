import json
import tempfile
import unittest
from pathlib import Path

from agent_ci_failure_correlator.github_audit import GitHubCurrentAuditOptions
from agent_ci_failure_correlator.inbox_audit import (
    audit_inbox_paths,
    render_inbox_action_plan_markdown,
    render_inbox_audit_json,
    render_inbox_audit_markdown,
)


class FakeInboxAuditClient:
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
        if repository == "org/current":
            return [
                {
                    "id": 101,
                    "workflow_id": 1,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "failure",
                    "html_url": "https://github.com/org/current/actions/runs/101",
                    "head_branch": "main",
                    "head_sha": "badsha",
                    "event": "push",
                    "run_number": 101,
                }
            ]
        if repository == "org/stale":
            return [
                {
                    "id": 201,
                    "workflow_id": 1,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/org/stale/actions/runs/201",
                    "head_branch": "main",
                    "head_sha": "goodsha",
                    "event": "push",
                    "run_number": 201,
                }
            ]
        return []

    def list_pull_requests(self, repository, *, page, per_page, state="open"):
        return []


class InboxAuditTests(unittest.TestCase):
    def test_audit_inbox_classifies_current_and_stale_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current.eml"
            stale = root / "stale.eml"
            current.write_text(
                "\n".join(
                    [
                        "Subject: [org/current] Run failed: CI - main",
                        "Workflow: CI",
                        "Job: tests",
                        "Branch: main",
                        "Run URL: https://github.com/org/current/actions/runs/9",
                        "",
                        "ModuleNotFoundError: No module named shared_auth",
                    ]
                ),
                encoding="utf-8",
            )
            stale.write_text(
                "\n".join(
                    [
                        "Subject: [org/stale] Run failed: CI - main",
                        "Workflow: CI",
                        "Job: tests",
                        "Branch: main",
                        "Run URL: https://github.com/org/stale/actions/runs/8",
                        "",
                        "ModuleNotFoundError: No module named shared_auth",
                    ]
                ),
                encoding="utf-8",
            )

            result = audit_inbox_paths([str(root)], client=FakeInboxAuditClient())

        self.assertEqual(["org/current", "org/stale"], result.repositories_from_inputs)
        self.assertEqual(1, len(result.current_events))
        self.assertEqual(1, len(result.stale_events))
        self.assertEqual("org/current", result.current_events[0].repository)
        self.assertTrue(result.has_current_problems)

    def test_render_inbox_audit_json_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.json"
            path.write_text(
                json.dumps(
                    {
                        "repository": "org/current",
                        "workflow": "CI",
                        "job_name": "tests",
                        "log": "ModuleNotFoundError: No module named shared_auth",
                    }
                ),
                encoding="utf-8",
            )
            result = audit_inbox_paths([str(path)], client=FakeInboxAuditClient())
            payload = json.loads(render_inbox_audit_json(result))

        self.assertEqual("agent-ci-failure-correlator.inbox-audit.v1", payload["schema"])
        self.assertEqual(1, payload["summary"]["current_event_count"])
        self.assertEqual(["org/current"], payload["current_problem_repositories"])

    def test_render_inbox_audit_markdown_clear_for_stale_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale.json"
            path.write_text(
                json.dumps(
                    {
                        "repository": "org/stale",
                        "workflow": "CI",
                        "job_name": "tests",
                        "log": "ModuleNotFoundError: No module named shared_auth",
                    }
                ),
                encoding="utf-8",
            )
            result = audit_inbox_paths(
                [str(path)],
                current_options=GitHubCurrentAuditOptions(include_open_prs=False),
                client=FakeInboxAuditClient(),
            )
            markdown = render_inbox_audit_markdown(result)

        self.assertIn("# CI Failure Inbox Audit", markdown)
        self.assertIn("Decision: CLEAR", markdown)
        self.assertIn("Likely Stale Failure Emails", markdown)

    def test_render_inbox_action_plan_prioritizes_current_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current.eml"
            stale = root / "stale.eml"
            current.write_text(
                "\n".join(
                    [
                        "Subject: [org/current] Run failed: CI - main",
                        "Workflow: CI",
                        "Job: tests",
                        "Branch: main",
                        "Run URL: https://github.com/org/current/actions/runs/9",
                        "",
                        "ModuleNotFoundError: No module named shared_auth",
                    ]
                ),
                encoding="utf-8",
            )
            stale.write_text(
                "\n".join(
                    [
                        "Subject: [org/stale] Run failed: CI - main",
                        "Workflow: CI",
                        "Job: tests",
                        "Branch: main",
                        "Run URL: https://github.com/org/stale/actions/runs/8",
                        "",
                        "ModuleNotFoundError: No module named shared_auth",
                    ]
                ),
                encoding="utf-8",
            )
            result = audit_inbox_paths([str(root)], client=FakeInboxAuditClient())
            plan = render_inbox_action_plan_markdown(result)

        self.assertIn("# CI Failure Inbox Action Plan", plan)
        self.assertIn("Decision: ACTION NEEDED", plan)
        self.assertIn("## Current Repair Work", plan)
        self.assertIn("org/current", plan)
        self.assertIn("## Archive Candidates", plan)
        self.assertIn("org/stale", plan)
        self.assertIn("## Agent Prompts", plan)
        self.assertIn("You are assigned to clear current CI failures for org/current", plan)

    def test_render_inbox_action_plan_clear_has_no_repair_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale.json"
            path.write_text(
                json.dumps(
                    {
                        "repository": "org/stale",
                        "workflow": "CI",
                        "job_name": "tests",
                        "log": "ModuleNotFoundError: No module named shared_auth",
                    }
                ),
                encoding="utf-8",
            )
            result = audit_inbox_paths(
                [str(path)],
                current_options=GitHubCurrentAuditOptions(include_open_prs=False),
                client=FakeInboxAuditClient(),
            )
            plan = render_inbox_action_plan_markdown(result)

        self.assertIn("Decision: CLEAR", plan)
        self.assertIn("No repair prompts are needed", plan)
        self.assertIn("archive `1` historical failure email", plan)

    def test_audit_inbox_does_not_mark_unmatched_workflow_as_current(self):
        class WorkflowMismatchClient(FakeInboxAuditClient):
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
                        "id": 301,
                        "workflow_id": 2,
                        "name": "Deploy",
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "https://github.com/org/current/actions/runs/301",
                        "head_branch": "main",
                        "head_sha": "baddeploy",
                        "event": "push",
                    }
                ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.eml"
            path.write_text(
                "\n".join(
                    [
                        "Subject: [org/current] Run failed: CI - main",
                        "Workflow: CI",
                        "Branch: main",
                        "Run URL: https://github.com/org/current/actions/runs/9",
                        "",
                        "ModuleNotFoundError: No module named shared_auth",
                    ]
                ),
                encoding="utf-8",
            )
            result = audit_inbox_paths([str(path)], client=WorkflowMismatchClient())

        self.assertEqual([], result.current_events)
        self.assertEqual(1, len(result.unknown_events))
        self.assertIn("none match", result.unknown_events[0].reason)


if __name__ == "__main__":
    unittest.main()
