"""Normalize, correlate, and report recurring CI failures across projects."""

from .api import analyze, analyze_paths
from .config import CorrelatorConfig
from .models import FailureEvent, RootCauseCluster, SourceRef

__all__ = [
    "CorrelatorConfig",
    "FailureEvent",
    "RootCauseCluster",
    "SourceRef",
    "analyze",
    "analyze_paths",
]

__version__ = "0.1.0"
