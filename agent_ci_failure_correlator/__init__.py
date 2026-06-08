"""Normalize, correlate, and report recurring CI failures across projects."""

from .api import analyze, analyze_paths
from .config import CorrelatorConfig
from .github_fetcher import GitHubClient, GitHubFetchOptions, GitHubFetchResult, fetch_failed_jobs
from .models import FailureEvent, RootCauseCluster, SourceRef
from .triage import TriageTask, build_triage_queue, render_queue_json, render_queue_markdown

__all__ = [
    "CorrelatorConfig",
    "FailureEvent",
    "GitHubClient",
    "GitHubFetchOptions",
    "GitHubFetchResult",
    "RootCauseCluster",
    "SourceRef",
    "TriageTask",
    "analyze",
    "analyze_paths",
    "build_triage_queue",
    "fetch_failed_jobs",
    "render_queue_json",
    "render_queue_markdown",
]

__version__ = "0.6.0"
