import json
import tempfile
import unittest
from pathlib import Path

from agent_ci_failure_correlator.config import CorrelatorConfig
from agent_ci_failure_correlator.parsers import discover_input_files, events_from_records, parse_file, parse_paths


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.config = CorrelatorConfig.default()

    def test_parse_plain_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "failure.log"
            path.write_text("ModuleNotFoundError: No module named shared_auth", encoding="utf-8")
            events = parse_file(path, self.config)
        self.assertEqual(len(events), 1)
        self.assertIn("python-import", events[0].root_cause_labels)

    def test_parse_github_actions_email(self):
        email_text = """From: GitHub Actions <notifications@github.com>
Subject: [org/api-service] Run failed: CI - main (abc1234)
Date: Mon, 08 Jun 2026 09:12:31 +0000
Content-Type: text/plain; charset=utf-8

Workflow: CI
Job: pytest
Branch: main
Commit: abc1234

Run URL: https://github.com/org/api-service/actions/runs/123456789

Traceback (most recent call last):
  File "/home/runner/work/api/tests/test_app.py", line 2, in <module>
    import shared_auth
ModuleNotFoundError: No module named 'shared_auth'
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "failure.eml"
            path.write_text(email_text, encoding="utf-8")
            events = parse_file(path, self.config)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source.format, "github-actions-email")
        self.assertEqual(events[0].source.repository, "org/api-service")
        self.assertEqual(events[0].source.workflow, "CI")
        self.assertEqual(events[0].source.run_id, "123456789")
        self.assertEqual(events[0].source.job_name, "pytest")
        self.assertEqual(events[0].metadata["branch"], "main")
        self.assertNotIn("Run failed", events[0].summary)
        self.assertIn("ModuleNotFoundError", events[0].summary)
        self.assertIn("python-import", events[0].root_cause_labels)

    def test_parse_github_actions_notification_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notification.txt"
            path.write_text(
                "Subject: [org/web] Run failed: CI - main\n"
                "Run URL: https://github.com/org/web/actions/runs/77\n"
                "Job: build\n"
                "Error: Cannot find module '@org/ui-theme'\n",
                encoding="utf-8",
            )
            events = parse_file(path, self.config)
        self.assertEqual(events[0].source.format, "github-actions-email")
        self.assertEqual(events[0].source.repository, "org/web")
        self.assertEqual(events[0].source.run_id, "77")
        self.assertIn("javascript-dependency", events[0].root_cause_labels)

    def test_parse_github_actions_subject_workflow_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notification.txt"
            path.write_text(
                "Subject: [org/api] Run failed: CI - release/v1 (abc123)\n"
                "Run URL: https://github.com/org/api/actions/runs/88\n"
                "ModuleNotFoundError: No module named shared_auth\n",
                encoding="utf-8",
            )
            events = parse_file(path, self.config)

        self.assertEqual(events[0].source.workflow, "CI")
        self.assertEqual(events[0].metadata["branch"], "release/v1")

    def test_parse_job_json_failed_step(self):
        data = {
            "repository": "org/a",
            "workflow": "CI",
            "job_name": "tests",
            "steps": [
                {"name": "setup", "conclusion": "success"},
                {"name": "pytest", "conclusion": "failure", "log": "ModuleNotFoundError: No module named shared_auth"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            events = parse_file(path, self.config)
        self.assertEqual(events[0].source.step_name, "pytest")

    def test_parse_workflow_run_jobs(self):
        data = {
            "repository": {"full_name": "org/a"},
            "workflow": "CI",
            "run_id": "42",
            "jobs": [
                {"name": "ok", "conclusion": "success"},
                {"name": "bad", "conclusion": "failure", "log": "Cannot find module '@org/ui-theme'"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            events = parse_file(path, self.config)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source.repository, "org/a")
        self.assertIn("javascript-dependency", events[0].root_cause_labels)

    def test_parse_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            path.write_text(
                json.dumps({"repository": "org/a", "log": "ModuleNotFoundError: No module named shared_auth"})
                + "\n"
                + json.dumps({"repository": "org/b", "log": "Cannot find module '@org/ui-theme'"}),
                encoding="utf-8",
            )
            events = parse_file(path, self.config)
        self.assertEqual(len(events), 2)

    def test_parse_junit_xml(self):
        xml = """<?xml version="1.0"?>
<testsuite repository="org/search" workflow="CI">
  <testcase classname="tests.test_index" name="test_refresh">
    <failure message="AssertionError">AssertionError: expected ready</failure>
  </testcase>
</testsuite>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "junit.xml"
            path.write_text(xml, encoding="utf-8")
            events = parse_file(path, self.config)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source.format, "junit-xml")

    def test_discover_input_files_recurses_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.log").write_text("error", encoding="utf-8")
            (root / "ignored.bin").write_text("error", encoding="utf-8")
            files = discover_input_files([str(root)])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "a.log")

    def test_parse_paths_returns_warnings_for_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{bad json", encoding="utf-8")
            events, warnings, inputs = parse_paths([str(path)], self.config)
        self.assertEqual(events, [])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(inputs), 1)

    def test_events_from_records(self):
        events = events_from_records(
            [{"repository": "org/a", "workflow": "CI", "log": "ModuleNotFoundError: No module named shared_auth"}],
            self.config,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source.repository, "org/a")

    def test_parse_exported_analysis_json(self):
        exported = {
            "events": [
                {
                    "event_id": "e1",
                    "source": {"path": "x", "repository": "org/a"},
                    "raw_text": "x",
                    "summary": "x",
                    "normalized_text": "x",
                    "tokens": ["x"],
                    "root_cause_labels": ["unknown"],
                    "rule_hits": [],
                }
            ],
            "clusters": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps(exported), encoding="utf-8")
            events = parse_file(path, self.config)
        self.assertEqual(events[0].event_id, "e1")


if __name__ == "__main__":
    unittest.main()
