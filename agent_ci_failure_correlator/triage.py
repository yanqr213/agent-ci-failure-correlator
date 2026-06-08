"""Repair queue generation for clustered CI failures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import AnalysisResult, FailureEvent, RootCauseCluster


@dataclass(frozen=True)
class TriageTask:
    """A deterministic repair task derived from one failure cluster."""

    task_id: str
    cluster_id: str
    priority: str
    score: int
    title: str
    owner_hint: str
    root_cause_labels: List[str]
    severity: str
    confidence: float
    event_count: int
    repository_count: int
    repositories: List[str]
    affected_jobs: List[Dict[str, str]]
    run_links: List[str]
    representative_summary: str
    suggested_actions: List[str]
    rationale: List[str]
    agent_prompt: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "cluster_id": self.cluster_id,
            "priority": self.priority,
            "score": self.score,
            "title": self.title,
            "owner_hint": self.owner_hint,
            "root_cause_labels": list(self.root_cause_labels),
            "severity": self.severity,
            "confidence": self.confidence,
            "event_count": self.event_count,
            "repository_count": self.repository_count,
            "repositories": list(self.repositories),
            "affected_jobs": [dict(item) for item in self.affected_jobs],
            "run_links": list(self.run_links),
            "representative_summary": self.representative_summary,
            "suggested_actions": list(self.suggested_actions),
            "rationale": list(self.rationale),
            "agent_prompt": self.agent_prompt,
        }


def build_triage_queue(result: AnalysisResult, *, max_tasks: Optional[int] = None) -> List[TriageTask]:
    """Return prioritized repair tasks for the clusters in an analysis result."""

    ranked = sorted(result.clusters, key=_task_sort_key)
    if max_tasks is not None:
        ranked = ranked[: max(0, max_tasks)]
    return [_cluster_to_task(cluster, index) for index, cluster in enumerate(ranked, start=1)]


def render_queue_json(result: AnalysisResult, *, max_tasks: Optional[int] = None) -> str:
    tasks = build_triage_queue(result, max_tasks=max_tasks)
    payload = {
        "summary": {
            **result.to_dict()["summary"],
            "task_count": len(tasks),
            "highest_priority": tasks[0].priority if tasks else "none",
        },
        "tasks": [task.to_dict() for task in tasks],
        "warnings": list(result.warnings),
        "inputs": list(result.inputs),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def render_queue_markdown(result: AnalysisResult, *, max_tasks: Optional[int] = None) -> str:
    tasks = build_triage_queue(result, max_tasks=max_tasks)
    summary = result.to_dict()["summary"]
    lines: List[str] = [
        "# CI Failure Repair Queue",
        "",
        f"- Failure events: `{summary['event_count']}`",
        f"- Clusters: `{summary['cluster_count']}`",
        f"- Repair tasks: `{len(tasks)}`",
        f"- Highest priority: `{tasks[0].priority if tasks else 'none'}`",
        "",
    ]
    if not tasks:
        lines.extend(["No repair tasks were generated.", ""])
        return "\n".join(lines)

    lines.extend(["## Queue", ""])
    for task in tasks:
        lines.extend(_render_task(task))
        lines.append("")
    if result.warnings:
        lines.extend(["## Parser Warnings", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines)


def _cluster_to_task(cluster: RootCauseCluster, index: int) -> TriageTask:
    score = _priority_score(cluster)
    labels = list(cluster.root_cause_labels)
    repositories = list(cluster.repositories)
    affected_jobs = _affected_jobs(cluster.events)
    run_links = _unique(event.source.url for event in cluster.events if event.source.url)
    title = _task_title(cluster)
    owner_hint = _owner_hint(cluster)
    rationale = _rationale(cluster, score)
    suggested_actions = list(cluster.suggested_actions)
    task_id = f"T{index:03d}"
    return TriageTask(
        task_id=task_id,
        cluster_id=cluster.cluster_id,
        priority=_priority_bucket(score),
        score=score,
        title=title,
        owner_hint=owner_hint,
        root_cause_labels=labels,
        severity=cluster.severity,
        confidence=cluster.confidence,
        event_count=cluster.event_count,
        repository_count=cluster.repository_count,
        repositories=repositories,
        affected_jobs=affected_jobs,
        run_links=run_links,
        representative_summary=cluster.representative_summary,
        suggested_actions=suggested_actions,
        rationale=rationale,
        agent_prompt=_agent_prompt(task_id, title, cluster, owner_hint, suggested_actions, run_links),
    )


def _task_sort_key(cluster: RootCauseCluster) -> tuple[int, int, int, str]:
    return (-_priority_score(cluster), -cluster.event_count, -cluster.repository_count, cluster.cluster_id)


def _priority_score(cluster: RootCauseCluster) -> int:
    severity_points = {"critical": 34, "error": 22, "warning": 10, "info": 0}.get(cluster.severity, 12)
    cross_repo_points = 34 if cluster.is_cross_repository else 0
    repository_points = min(24, cluster.repository_count * 8)
    event_points = min(20, cluster.event_count * 4)
    confidence_points = int(round(cluster.confidence * 20))
    return min(100, severity_points + cross_repo_points + repository_points + event_points + confidence_points)


def _priority_bucket(score: int) -> str:
    if score >= 92:
        return "P0"
    if score >= 70:
        return "P1"
    if score >= 45:
        return "P2"
    return "P3"


def _task_title(cluster: RootCauseCluster) -> str:
    label = cluster.root_cause_labels[0] if cluster.root_cause_labels else "unknown"
    scope = "cross-repository" if cluster.is_cross_repository else "single-repository"
    return f"Fix {scope} {label} CI failures"


def _owner_hint(cluster: RootCauseCluster) -> str:
    labels = set(cluster.root_cause_labels)
    if labels & {"runner-environment", "container", "network", "auth-permission"}:
        return "platform/ci-infrastructure"
    if labels & {"dependency-version", "python-import", "javascript-dependency"}:
        return "runtime/dependency-owner"
    if labels & {"test-assertion", "lint-style", "type-check"}:
        return "repository-maintainer"
    if cluster.is_cross_repository:
        return "shared-tooling-owner"
    return cluster.repositories[0] if cluster.repositories else "unassigned"


def _rationale(cluster: RootCauseCluster, score: int) -> List[str]:
    reasons = [
        f"priority score {score}",
        f"{cluster.event_count} failure event(s)",
        f"{cluster.repository_count} affected repository/repositories",
        f"confidence {cluster.confidence:.2f}",
    ]
    if cluster.is_cross_repository:
        reasons.append("cross-repository repeat should be batch-fixed before isolated failures")
    if cluster.root_cause_labels and cluster.root_cause_labels != ["unknown"]:
        reasons.append("root-cause labels: " + ", ".join(cluster.root_cause_labels))
    return reasons


def _affected_jobs(events: Sequence[FailureEvent]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    seen = set()
    for event in events:
        source = event.source
        key = (source.repository, source.workflow, source.job_name, source.run_id, source.url)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "repository": source.repository,
                "workflow": source.workflow,
                "job_name": source.job_name,
                "run_id": source.run_id,
                "url": source.url,
                "event_id": event.event_id,
            }
        )
    rows.sort(key=lambda item: (item["repository"], item["workflow"], item["job_name"], item["run_id"], item["event_id"]))
    return rows


def _agent_prompt(
    task_id: str,
    title: str,
    cluster: RootCauseCluster,
    owner_hint: str,
    suggested_actions: Sequence[str],
    run_links: Sequence[str],
) -> str:
    lines = [
        f"You are assigned repair task {task_id}: {title}.",
        f"Cluster: {cluster.cluster_id}; priority inputs: {cluster.event_count} failures across {cluster.repository_count} repositories.",
        f"Owner hint: {owner_hint}.",
        "Affected repositories: " + (", ".join(cluster.repositories) if cluster.repositories else "unknown"),
        "Root-cause labels: " + (", ".join(cluster.root_cause_labels) if cluster.root_cause_labels else "unknown"),
        "Representative failure summary:",
        cluster.representative_summary.strip(),
        "Suggested actions:",
    ]
    for action in suggested_actions:
        lines.append(f"- {action}")
    if run_links:
        lines.append("Run links:")
        for link in run_links[:5]:
            lines.append(f"- {link}")
    lines.extend(
        [
            "Before editing code, reproduce or inspect the representative failure, then apply the smallest shared fix.",
            "After the fix, run focused tests first, then the affected CI workflow or equivalent local smoke command.",
        ]
    )
    return "\n".join(lines)


def _render_task(task: TriageTask) -> List[str]:
    lines = [
        f"### {task.task_id} [{task.priority}] {task.title}",
        "",
        f"- Cluster: `{task.cluster_id}`",
        f"- Score: `{task.score}`",
        f"- Owner hint: `{task.owner_hint}`",
        f"- Severity: `{task.severity}`",
        f"- Confidence: `{task.confidence:.2f}`",
        f"- Scope: `{task.event_count}` events across `{task.repository_count}` repositories",
        f"- Labels: {', '.join('`' + label + '`' for label in task.root_cause_labels) or '`unknown`'}",
        f"- Repositories: {', '.join('`' + repo + '`' for repo in task.repositories) or '`unknown`'}",
        "",
        "**Why this priority**",
        "",
    ]
    for item in task.rationale:
        lines.append(f"- {item}")
    lines.extend(["", "**Suggested actions**", ""])
    for action in task.suggested_actions:
        lines.append(f"- {action}")
    lines.extend(["", "**Affected jobs**", "", "| Repository | Workflow | Job | Run |", "| --- | --- | --- | --- |"])
    for row in task.affected_jobs:
        run = f"[{row['run_id']}]({row['url']})" if row["url"] and row["run_id"] else (row["run_id"] or "-")
        lines.append(f"| `{row['repository'] or '-'}` | `{row['workflow'] or '-'}` | `{row['job_name'] or '-'}` | {run} |")
    lines.extend(["", "**Agent prompt**", "", "```text", task.agent_prompt, "```"])
    return lines


def _unique(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
