"""Audit current GitHub Actions health for default branches and open pull requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .github_fetcher import (
    GitHubClient,
    GitHubRepositoryDiscoveryOptions,
    GitHubRepositoryDiscoveryResult,
    discover_repositories,
)

SUCCESS_CONCLUSIONS = {"success", "skipped", "neutral"}


@dataclass(frozen=True)
class GitHubCurrentAuditOptions:
    """Options for auditing the current GitHub Actions state."""

    include_open_prs: bool = True
    per_repo_run_limit: int = 100
    pr_limit: int = 100
    max_pr_pages: int = 1
    include_pending: bool = True


@dataclass
class CurrentWorkflowHead:
    """Latest workflow run for a repository scope."""

    repository: str
    scope: str
    workflow: str
    status: str
    conclusion: str
    url: str
    created_at: str
    updated_at: str
    head_branch: str
    head_sha: str
    event: str
    run_id: str
    run_number: Any = None

    @property
    def is_success(self) -> bool:
        return self.status == "completed" and self.conclusion in SUCCESS_CONCLUSIONS

    @property
    def is_problem(self) -> bool:
        if self.status != "completed":
            return True
        return self.conclusion not in SUCCESS_CONCLUSIONS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": self.repository,
            "scope": self.scope,
            "workflow": self.workflow,
            "status": self.status,
            "conclusion": self.conclusion,
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "head_branch": self.head_branch,
            "head_sha": self.head_sha,
            "event": self.event,
            "run_id": self.run_id,
            "run_number": self.run_number,
            "is_success": self.is_success,
            "is_problem": self.is_problem,
        }


@dataclass
class CurrentPullRequest:
    """Open pull request inspected during the audit."""

    number: int
    title: str
    url: str
    head_ref: str
    head_sha: str
    base_ref: str
    draft: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "head_ref": self.head_ref,
            "head_sha": self.head_sha,
            "base_ref": self.base_ref,
            "draft": self.draft,
        }


@dataclass
class RepositoryCurrentAudit:
    """Current Actions audit for one repository."""

    repository: str
    default_branch: str
    workflow_heads: List[CurrentWorkflowHead] = field(default_factory=list)
    open_pull_requests: List[CurrentPullRequest] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def problem_heads(self) -> List[CurrentWorkflowHead]:
        return [head for head in self.workflow_heads if head.is_problem]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": self.repository,
            "default_branch": self.default_branch,
            "workflow_heads": [head.to_dict() for head in self.workflow_heads],
            "problem_heads": [head.to_dict() for head in self.problem_heads],
            "open_pull_requests": [pr.to_dict() for pr in self.open_pull_requests],
            "warnings": list(self.warnings),
        }


@dataclass
class GitHubCurrentAuditResult:
    """Current GitHub Actions health across repositories."""

    repositories: List[RepositoryCurrentAudit] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    discovered: Optional[GitHubRepositoryDiscoveryResult] = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    @property
    def problem_heads(self) -> List[CurrentWorkflowHead]:
        return [head for repo in self.repositories for head in repo.problem_heads]

    def to_dict(self) -> Dict[str, Any]:
        problems = self.problem_heads
        return {
            "schema": "agent-ci-failure-correlator.current-actions.v1",
            "generated_at": self.generated_at,
            "summary": {
                "repository_count": len(self.repositories),
                "workflow_head_count": sum(len(repo.workflow_heads) for repo in self.repositories),
                "open_pull_request_count": sum(len(repo.open_pull_requests) for repo in self.repositories),
                "problem_count": len(problems),
                "has_current_problems": bool(problems),
            },
            "warnings": list(self.warnings),
            "repositories": [repo.to_dict() for repo in self.repositories],
            "problems": [head.to_dict() for head in problems],
            "discovered": _discovery_dict(self.discovered),
        }


def audit_current_actions(
    repositories: Sequence[str],
    options: Optional[GitHubCurrentAuditOptions] = None,
    *,
    client: Optional[GitHubClient] = None,
) -> GitHubCurrentAuditResult:
    """Audit current default-branch and open-PR workflow heads for repositories."""

    opts = options or GitHubCurrentAuditOptions()
    api = client or GitHubClient()
    result = GitHubCurrentAuditResult()
    for repository in repositories:
        try:
            audit = _audit_repository(repository, opts, api)
        except Exception as exc:  # noqa: BLE001 - warnings should keep multi-repo audits moving.
            result.warnings.append(f"{repository}: {exc}")
            continue
        result.repositories.append(audit)
        result.warnings.extend(audit.warnings)
    return result


def audit_current_actions_for_owners(
    owners: Sequence[str],
    options: Optional[GitHubCurrentAuditOptions] = None,
    discovery_options: Optional[GitHubRepositoryDiscoveryOptions] = None,
    *,
    client: Optional[GitHubClient] = None,
) -> GitHubCurrentAuditResult:
    """Discover owner repositories and audit their current Actions health."""

    api = client or GitHubClient()
    discovered = discover_repositories(owners, discovery_options, client=api)
    result = audit_current_actions(discovered.repositories, options, client=api)
    result.discovered = discovered
    result.warnings = discovered.warnings + result.warnings
    return result


def render_current_audit_json(result: GitHubCurrentAuditResult, *, pretty: bool = True) -> str:
    """Render current Actions audit as machine-readable JSON."""

    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if pretty else None, sort_keys=True) + "\n"


def render_current_audit_markdown(result: GitHubCurrentAuditResult) -> str:
    """Render current Actions audit as a compact human triage report."""

    data = result.to_dict()
    summary = data["summary"]
    decision = "ACTION NEEDED" if summary["has_current_problems"] else "CLEAR"
    lines = [
        "# Current GitHub Actions Audit",
        "",
        f"- Generated: {result.generated_at}",
        f"- Decision: {decision}",
        f"- Repositories scanned: {summary['repository_count']}",
        f"- Workflow heads checked: {summary['workflow_head_count']}",
        f"- Open pull requests checked: {summary['open_pull_request_count']}",
        f"- Current problem heads: {summary['problem_count']}",
        "",
    ]
    if result.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")
    if not result.problem_heads:
        lines.extend(
            [
                "## Result",
                "",
                "No current default-branch or open-pull-request GitHub Actions problems were found.",
                "Historical failed runs may still exist, but they are not current red heads for the scanned scopes.",
                "",
            ]
        )
        return "\n".join(lines)
    lines.extend(["## Current Problems", ""])
    for head in result.problem_heads:
        status = head.conclusion or head.status or "unknown"
        lines.append(
            f"- **{head.repository}** `{head.scope}` `{head.workflow}`: {status} "
            f"on `{head.head_branch or 'unknown'}` `{head.head_sha[:12]}`"
        )
        if head.url:
            lines.append(f"  - Run: {head.url}")
    lines.append("")
    return "\n".join(lines)


def _audit_repository(repository: str, options: GitHubCurrentAuditOptions, client: GitHubClient) -> RepositoryCurrentAudit:
    repo_data = client.get_repository(repository)
    default_branch = str(repo_data.get("default_branch") or "main")
    audit = RepositoryCurrentAudit(repository=repository, default_branch=default_branch)
    default_runs = client.list_current_workflow_runs(
        repository,
        page=1,
        per_page=max(1, options.per_repo_run_limit),
        branch=default_branch,
        exclude_pull_requests=True,
    )
    audit.workflow_heads.extend(_latest_workflow_heads(repository, f"default:{default_branch}", default_runs))
    if options.include_open_prs:
        for pr in _open_pull_requests(repository, client, options):
            audit.open_pull_requests.append(pr)
            if not pr.head_sha:
                audit.warnings.append(f"{repository} PR #{pr.number}: missing head sha")
                continue
            runs = client.list_current_workflow_runs(
                repository,
                page=1,
                per_page=max(1, options.per_repo_run_limit),
                head_sha=pr.head_sha,
            )
            audit.workflow_heads.extend(_latest_workflow_heads(repository, f"open-pr:{pr.number}", runs))
    if not options.include_pending:
        audit.workflow_heads = [head for head in audit.workflow_heads if head.status == "completed"]
    return audit


def _open_pull_requests(
    repository: str,
    client: GitHubClient,
    options: GitHubCurrentAuditOptions,
) -> List[CurrentPullRequest]:
    prs: List[CurrentPullRequest] = []
    for page in range(1, max(1, options.max_pr_pages) + 1):
        batch = client.list_pull_requests(repository, page=page, per_page=min(100, max(1, options.pr_limit)), state="open")
        if not batch:
            break
        for pr in batch:
            prs.append(_pull_request_from_mapping(pr))
            if len(prs) >= max(1, options.pr_limit):
                return prs
        if len(batch) < min(100, max(1, options.pr_limit)):
            break
    return prs


def _pull_request_from_mapping(data: Mapping[str, Any]) -> CurrentPullRequest:
    head = data.get("head") if isinstance(data.get("head"), Mapping) else {}
    base = data.get("base") if isinstance(data.get("base"), Mapping) else {}
    return CurrentPullRequest(
        number=int(data.get("number") or 0),
        title=str(data.get("title") or ""),
        url=str(data.get("html_url") or ""),
        head_ref=str(head.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        base_ref=str(base.get("ref") or ""),
        draft=bool(data.get("draft")),
    )


def _latest_workflow_heads(repository: str, scope: str, runs: Sequence[Mapping[str, Any]]) -> List[CurrentWorkflowHead]:
    latest: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for run in runs:
        workflow_key = str(run.get("workflow_id") or run.get("name") or run.get("workflow_name") or "")
        branch_key = str(run.get("head_branch") or "")
        key = (workflow_key, branch_key)
        if key not in latest:
            latest[key] = run
    return [_workflow_head_from_mapping(repository, scope, run) for run in latest.values()]


def _workflow_head_from_mapping(repository: str, scope: str, run: Mapping[str, Any]) -> CurrentWorkflowHead:
    return CurrentWorkflowHead(
        repository=repository,
        scope=scope,
        workflow=str(run.get("name") or run.get("workflow_name") or ""),
        status=str(run.get("status") or ""),
        conclusion=str(run.get("conclusion") or ""),
        url=str(run.get("html_url") or ""),
        created_at=str(run.get("created_at") or ""),
        updated_at=str(run.get("updated_at") or ""),
        head_branch=str(run.get("head_branch") or ""),
        head_sha=str(run.get("head_sha") or ""),
        event=str(run.get("event") or ""),
        run_id=str(run.get("id") or ""),
        run_number=run.get("run_number"),
    )


def _discovery_dict(discovered: Optional[GitHubRepositoryDiscoveryResult]) -> Dict[str, Any]:
    if not discovered:
        return {}
    return {
        "owners": list(discovered.owners),
        "repositories": list(discovered.repositories),
        "warnings": list(discovered.warnings),
    }
