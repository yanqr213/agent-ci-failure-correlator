"""Normalize, correlate, and report recurring CI failures across projects."""

from .api import analyze, analyze_paths
from .config import CorrelatorConfig
from .github_fetcher import (
    GitHubClient,
    GitHubFetchOptions,
    GitHubFetchResult,
    GitHubRepositoryDiscoveryOptions,
    GitHubRepositoryDiscoveryResult,
    discover_repositories,
    fetch_failed_jobs,
    fetch_failed_jobs_for_owners,
)
from .github_audit import (
    GitHubCurrentAuditOptions,
    GitHubCurrentAuditResult,
    audit_current_actions,
    audit_current_actions_for_owners,
    render_current_audit_json,
    render_current_audit_markdown,
)
from .inbox_audit import (
    InboxAuditResult,
    InboxEventAudit,
    audit_inbox_paths,
    render_inbox_action_plan_markdown,
    render_inbox_audit_json,
    render_inbox_audit_markdown,
)
from .models import FailureEvent, RootCauseCluster, SourceRef
from .triage import TriageTask, build_triage_queue, render_queue_json, render_queue_markdown

__all__ = [
    "CorrelatorConfig",
    "FailureEvent",
    "GitHubClient",
    "GitHubCurrentAuditOptions",
    "GitHubCurrentAuditResult",
    "GitHubFetchOptions",
    "GitHubFetchResult",
    "GitHubRepositoryDiscoveryOptions",
    "GitHubRepositoryDiscoveryResult",
    "InboxAuditResult",
    "InboxEventAudit",
    "RootCauseCluster",
    "SourceRef",
    "TriageTask",
    "analyze",
    "analyze_paths",
    "audit_current_actions",
    "audit_current_actions_for_owners",
    "audit_inbox_paths",
    "build_triage_queue",
    "discover_repositories",
    "fetch_failed_jobs",
    "fetch_failed_jobs_for_owners",
    "render_current_audit_json",
    "render_current_audit_markdown",
    "render_inbox_action_plan_markdown",
    "render_inbox_audit_json",
    "render_inbox_audit_markdown",
    "render_queue_json",
    "render_queue_markdown",
]

__version__ = "0.10.0"
