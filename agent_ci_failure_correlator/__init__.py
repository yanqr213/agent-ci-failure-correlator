"""Normalize, correlate, and report recurring CI failures across projects."""

from .api import analyze, analyze_paths
from .config import CorrelatorConfig
from .github_fetcher import GitHubClient, GitHubFetchOptions, GitHubFetchResult, fetch_failed_jobs
from .models import FailureEvent, RootCauseCluster, SourceRef

__all__ = [
    "CorrelatorConfig",
    "FailureEvent",
    "GitHubClient",
    "GitHubFetchOptions",
    "GitHubFetchResult",
    "RootCauseCluster",
    "SourceRef",
    "analyze",
    "analyze_paths",
    "fetch_failed_jobs",
]

__version__ = "0.5.0"
