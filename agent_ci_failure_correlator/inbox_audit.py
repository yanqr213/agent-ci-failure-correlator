"""Audit exported CI failure inboxes against current GitHub Actions heads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, List, Optional, Sequence

from .api import analyze_paths
from .config import CorrelatorConfig
from .github_audit import CurrentWorkflowHead, GitHubCurrentAuditOptions, GitHubCurrentAuditResult, audit_current_actions
from .github_fetcher import GitHubClient
from .models import AnalysisResult, FailureEvent


INBOX_AUDIT_SCHEMA = "agent-ci-failure-correlator.inbox-audit.v1"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass
class InboxEventAudit:
    """One historical failure event classified against current Actions state."""

    event_id: str
    repository: str
    source_path: str
    workflow: str
    job_name: str
    branch: str
    run_id: str
    url: str
    status: str
    reason: str
    summary: str
    root_cause_labels: List[str] = field(default_factory=list)
    current_problem_heads: List[CurrentWorkflowHead] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "repository": self.repository,
            "source_path": self.source_path,
            "workflow": self.workflow,
            "job_name": self.job_name,
            "branch": self.branch,
            "run_id": self.run_id,
            "url": self.url,
            "status": self.status,
            "reason": self.reason,
            "summary": self.summary,
            "root_cause_labels": list(self.root_cause_labels),
            "current_problem_heads": [head.to_dict() for head in self.current_problem_heads],
        }


@dataclass
class InboxAuditResult:
    """Historical inbox analysis joined with current GitHub Actions state."""

    analysis: AnalysisResult
    current_audit: GitHubCurrentAuditResult
    event_audits: List[InboxEventAudit]
    repositories_from_inputs: List[str]
    warnings: List[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    @property
    def current_events(self) -> List[InboxEventAudit]:
        return [item for item in self.event_audits if item.status == "current"]

    @property
    def stale_events(self) -> List[InboxEventAudit]:
        return [item for item in self.event_audits if item.status == "stale"]

    @property
    def unknown_events(self) -> List[InboxEventAudit]:
        return [item for item in self.event_audits if item.status == "unknown"]

    @property
    def has_current_problems(self) -> bool:
        return bool(self.current_events)

    def to_dict(self) -> Dict[str, Any]:
        current_problem_repositories = sorted({item.repository for item in self.current_events if item.repository})
        stale_repositories = sorted({item.repository for item in self.stale_events if item.repository})
        unknown_repositories = sorted({item.repository for item in self.unknown_events if item.repository})
        return {
            "schema": INBOX_AUDIT_SCHEMA,
            "generated_at": self.generated_at,
            "summary": {
                "input_count": len(self.analysis.inputs),
                "event_count": len(self.event_audits),
                "repository_count": len(self.repositories_from_inputs),
                "audited_repository_count": len(self.current_audit.repositories),
                "current_event_count": len(self.current_events),
                "stale_event_count": len(self.stale_events),
                "unknown_event_count": len(self.unknown_events),
                "current_repository_count": len(current_problem_repositories),
                "stale_repository_count": len(stale_repositories),
                "unknown_repository_count": len(unknown_repositories),
                "cluster_count": len(self.analysis.clusters),
                "cross_repository_cluster_count": sum(1 for cluster in self.analysis.clusters if cluster.is_cross_repository),
                "has_current_problems": self.has_current_problems,
                "warning_count": len(self.warnings),
            },
            "repositories_from_inputs": list(self.repositories_from_inputs),
            "current_problem_repositories": current_problem_repositories,
            "stale_repositories": stale_repositories,
            "unknown_repositories": unknown_repositories,
            "warnings": list(self.warnings),
            "event_audits": [item.to_dict() for item in self.event_audits],
            "current_audit": self.current_audit.to_dict(),
        }


def audit_inbox_paths(
    paths: Sequence[str],
    *,
    config: Optional[CorrelatorConfig] = None,
    config_path: Optional[str] = None,
    current_options: Optional[GitHubCurrentAuditOptions] = None,
    client: Optional[GitHubClient] = None,
) -> InboxAuditResult:
    """Parse exported failure inputs, audit referenced repositories, and classify events."""

    analysis = analyze_paths(paths, config=config, config_path=config_path)
    repositories = _repositories_from_events(analysis.events)
    warnings = list(analysis.warnings)
    if repositories:
        current = audit_current_actions(repositories, current_options, client=client)
        warnings.extend(current.warnings)
    else:
        current = GitHubCurrentAuditResult()
        warnings.append("No owner/name repositories were found in the input events.")
    event_audits = _classify_events(analysis.events, current)
    return InboxAuditResult(
        analysis=analysis,
        current_audit=current,
        event_audits=event_audits,
        repositories_from_inputs=repositories,
        warnings=warnings,
    )


def render_inbox_audit_json(result: InboxAuditResult, *, pretty: bool = True) -> str:
    """Render inbox audit as stable machine-readable JSON."""

    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if pretty else None, sort_keys=True) + "\n"


def render_inbox_audit_markdown(result: InboxAuditResult) -> str:
    """Render inbox audit as a compact human triage report."""

    data = result.to_dict()
    summary = data["summary"]
    if summary["current_event_count"]:
        decision = "ACTION NEEDED"
    elif summary["unknown_event_count"] or result.warnings:
        decision = "REVIEW NEEDED"
    else:
        decision = "CLEAR"
    lines = [
        "# CI Failure Inbox Audit",
        "",
        f"- Generated: {result.generated_at}",
        f"- Decision: {decision}",
        f"- Inputs parsed: {summary['input_count']}",
        f"- Historical failure events: {summary['event_count']}",
        f"- Repositories extracted: {summary['repository_count']}",
        f"- Repositories audited: {summary['audited_repository_count']}",
        f"- Current events: {summary['current_event_count']}",
        f"- Stale events: {summary['stale_event_count']}",
        f"- Unknown events: {summary['unknown_event_count']}",
        "",
    ]
    if result.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")
    if result.current_events:
        lines.extend(["## Current Problems From Inbox", ""])
        for repository, events in _events_by_repository(result.current_events).items():
            heads = _problem_heads_for_events(events)
            lines.append(f"### {repository}")
            lines.append("")
            lines.append(f"- Historical events in inbox: {len(events)}")
            lines.append(f"- Current problem heads: {len(heads)}")
            for head in heads[:5]:
                state = head.conclusion or head.status or "unknown"
                lines.append(
                    f"  - `{head.scope}` `{head.workflow}`: {state} on `{head.head_branch or 'unknown'}` "
                    f"`{head.head_sha[:12]}`"
                )
                if head.url:
                    lines.append(f"    - Run: {head.url}")
            lines.append("")
            for event in events[:3]:
                labels = ", ".join(event.root_cause_labels) or "unlabeled"
                lines.append(f"- `{event.workflow or 'unknown workflow'}` `{event.job_name or 'unknown job'}` [{labels}]")
                if event.summary:
                    lines.append(f"  - {event.summary.splitlines()[0][:180]}")
            lines.append("")
    if result.stale_events:
        lines.extend(["## Likely Stale Failure Emails", ""])
        for repository, events in _events_by_repository(result.stale_events).items():
            lines.append(f"- **{repository}**: {len(events)} historical events; current audited workflow heads are clear.")
        lines.append("")
    if result.unknown_events:
        lines.extend(["## Unknown Or Unchecked Events", ""])
        for event in result.unknown_events[:20]:
            repo = event.repository or "unknown"
            lines.append(f"- `{repo}` from `{event.source_path}`: {event.reason}")
        if len(result.unknown_events) > 20:
            lines.append(f"- ... {len(result.unknown_events) - 20} more")
        lines.append("")
    if not result.current_events and not result.unknown_events:
        lines.extend(
            [
                "## Result",
                "",
                "All repository-scoped failure events from the inbox look stale against the audited current GitHub Actions heads.",
                "You can archive or de-prioritize these emails unless you need historical incident analysis.",
                "",
            ]
        )
    lines.extend(
        [
            "## Recommended Next Step",
            "",
            "- If decision is `ACTION NEEDED`, run `fetch-github` and `analyze` for the current problem repositories to cluster live logs.",
            "- If decision is `CLEAR`, treat the messages as historical noise and archive them after confirming the scanned scope.",
            "- If decision is `REVIEW NEEDED`, fix missing repository metadata, authentication, rate limits, or audit warnings first.",
            "",
        ]
    )
    return "\n".join(lines)


def _repositories_from_events(events: Sequence[FailureEvent]) -> List[str]:
    seen = set()
    repositories: List[str] = []
    for event in events:
        repository = event.source.repository.strip()
        if not _is_owner_repository(repository):
            continue
        key = repository.lower()
        if key in seen:
            continue
        seen.add(key)
        repositories.append(repository)
    return sorted(repositories, key=str.lower)


def _classify_events(events: Sequence[FailureEvent], current: GitHubCurrentAuditResult) -> List[InboxEventAudit]:
    audits_by_repository = {audit.repository.lower(): audit for audit in current.repositories}
    result: List[InboxEventAudit] = []
    for event in events:
        repository = event.source.repository.strip()
        branch = str(event.metadata.get("branch") or event.metadata.get("ref") or "")
        status = "unknown"
        reason = "event repository is missing or is not in owner/name format"
        heads: List[CurrentWorkflowHead] = []
        if _is_owner_repository(repository):
            repo_audit = audits_by_repository.get(repository.lower())
            if repo_audit is None:
                reason = "repository could not be audited"
            else:
                heads = _matching_problem_heads(event, repo_audit.problem_heads, branch)
                if heads:
                    status = "current"
                    reason = "matching workflow or branch still has current failing or pending GitHub Actions heads"
                elif repo_audit.problem_heads:
                    reason = "repository has current problems, but none match this event workflow or branch"
                else:
                    status = "stale"
                    reason = "repository current default branch and open PR workflow heads are clear"
        result.append(
            InboxEventAudit(
                event_id=event.event_id,
                repository=repository,
                source_path=event.source.path,
                workflow=event.source.workflow,
                job_name=event.source.job_name,
                branch=branch,
                run_id=event.source.run_id,
                url=event.source.url,
                status=status,
                reason=reason,
                summary=event.summary,
                root_cause_labels=list(event.root_cause_labels),
                current_problem_heads=heads,
            )
        )
    return result


def _matching_problem_heads(
    event: FailureEvent,
    problem_heads: Sequence[CurrentWorkflowHead],
    branch: str,
) -> List[CurrentWorkflowHead]:
    event_workflow = _norm(event.source.workflow)
    event_branch = _norm(branch)
    if not event_workflow and not event_branch:
        return list(problem_heads)
    matches = []
    for head in problem_heads:
        workflow_matches = not event_workflow or _norm(head.workflow) == event_workflow
        branch_matches = not event_branch or _norm(head.head_branch) == event_branch or _norm(head.scope) == f"default:{event_branch}"
        if workflow_matches and branch_matches:
            matches.append(head)
    return matches


def _is_owner_repository(value: str) -> bool:
    return bool(value and REPOSITORY_RE.match(value))


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def _events_by_repository(events: Sequence[InboxEventAudit]) -> Dict[str, List[InboxEventAudit]]:
    grouped: Dict[str, List[InboxEventAudit]] = {}
    for event in events:
        grouped.setdefault(event.repository or "unknown", []).append(event)
    return dict(sorted(grouped.items(), key=lambda item: item[0].lower()))


def _problem_heads_for_events(events: Sequence[InboxEventAudit]) -> List[CurrentWorkflowHead]:
    seen = set()
    heads: List[CurrentWorkflowHead] = []
    for event in events:
        for head in event.current_problem_heads:
            key = (head.repository, head.scope, head.workflow, head.run_id)
            if key in seen:
                continue
            seen.add(key)
            heads.append(head)
    return heads
