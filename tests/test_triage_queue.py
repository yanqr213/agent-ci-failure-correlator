import json
import tempfile
import unittest
from pathlib import Path

from agent_ci_failure_correlator.api import analyze
from agent_ci_failure_correlator.cli import EXIT_SUCCESS, main
from agent_ci_failure_correlator.config import CorrelatorConfig
from agent_ci_failure_correlator.models import FailureEvent, SourceRef
from agent_ci_failure_correlator.triage import build_triage_queue, render_queue_json, render_queue_markdown


def make_event(event_id, repo, text, labels, *, url=""):
    return FailureEvent(
        event_id=event_id,
        source=SourceRef(
            path=f"{event_id}.log",
            repository=repo,
            workflow="CI",
            run_id=event_id.replace("e", "10"),
            job_name="tests",
            url=url,
        ),
        raw_text=text,
        summary=text,
        normalized_text=text.lower(),
        tokens=[token.strip(":'").lower() for token in text.split()],
        root_cause_labels=labels,
        rule_hits=labels,
        language="python" if "python-import" in labels else "unknown",
    )


class TriageQueueTests(unittest.TestCase):
    def test_build_triage_queue_prioritizes_cross_repo_cluster(self):
        result = analyze(
            [
                make_event("e1", "org/api", "ModuleNotFoundError: No module named shared_auth", ["python-import"], url="https://github.com/org/api/actions/runs/1"),
                make_event("e2", "org/web", "ModuleNotFoundError: No module named shared_auth", ["python-import"], url="https://github.com/org/web/actions/runs/2"),
                make_event("e3", "org/docs", "AssertionError expected 1 got 2", ["test-assertion"]),
            ],
            CorrelatorConfig(similarity_threshold=0.55),
        )

        tasks = build_triage_queue(result)

        self.assertGreaterEqual(len(tasks), 2)
        self.assertEqual(tasks[0].priority, "P0")
        self.assertIn("cross-repository", tasks[0].title)
        self.assertEqual(tasks[0].owner_hint, "runtime/dependency-owner")
        self.assertEqual(tasks[0].repository_count, 2)
        self.assertIn("shared_auth", tasks[0].agent_prompt)
        self.assertEqual(tasks[0].run_links[0], "https://github.com/org/api/actions/runs/1")

    def test_render_queue_json_is_agent_readable(self):
        result = analyze(
            [make_event("e1", "org/api", "ModuleNotFoundError: No module named shared_auth", ["python-import"])],
            CorrelatorConfig.default(),
        )

        data = json.loads(render_queue_json(result, max_tasks=1))

        self.assertEqual(data["summary"]["task_count"], 1)
        self.assertEqual(len(data["tasks"]), 1)
        self.assertIn("agent_prompt", data["tasks"][0])
        self.assertIn("affected_jobs", data["tasks"][0])

    def test_render_queue_markdown_contains_prompt_and_jobs(self):
        result = analyze(
            [make_event("e1", "org/api", "ModuleNotFoundError: No module named shared_auth", ["python-import"])],
            CorrelatorConfig.default(),
        )

        markdown = render_queue_markdown(result)

        self.assertIn("# CI Failure Repair Queue", markdown)
        self.assertIn("**Affected jobs**", markdown)
        self.assertIn("**Agent prompt**", markdown)

    def test_cli_analyze_writes_queue_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "failures.jsonl"
            queue_path = root / "reports" / "queue.md"
            json_path = root / "reports" / "queue.json"
            input_path.write_text(
                "\n".join(
                    [
                        json.dumps({"repository": "org/api", "log": "ModuleNotFoundError: No module named shared_auth"}),
                        json.dumps({"repository": "org/web", "log": "ModuleNotFoundError: No module named shared_auth"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            markdown_code = main(["analyze", str(input_path), "--format", "queue", "--output", str(queue_path), "--max-tasks", "1"])
            json_code = main(["analyze", str(input_path), "--format", "queue-json", "--output", str(json_path), "--max-tasks", "1"])

            self.assertEqual(markdown_code, EXIT_SUCCESS)
            self.assertEqual(json_code, EXIT_SUCCESS)
            self.assertIn("CI Failure Repair Queue", queue_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(json.loads(json_path.read_text(encoding="utf-8"))["tasks"]))


if __name__ == "__main__":
    unittest.main()
