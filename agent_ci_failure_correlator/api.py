"""Importable API for CI failure correlation."""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from .clusterer import cluster_failures
from .config import CorrelatorConfig, load_config
from .models import AnalysisResult, FailureEvent
from .parsers import events_from_records, parse_paths


def analyze(events: Iterable[FailureEvent], config: Optional[CorrelatorConfig] = None) -> AnalysisResult:
    """Cluster already-normalized failure events."""

    cfg = config or CorrelatorConfig.default()
    event_list = list(events)
    clusters = cluster_failures(event_list, cfg)
    return AnalysisResult(events=event_list, clusters=clusters, warnings=[], inputs=[], config=cfg.to_dict())


def analyze_records(records: Iterable[Mapping[str, object]], config: Optional[CorrelatorConfig] = None) -> AnalysisResult:
    """Normalize and cluster dictionary records from another tool."""

    cfg = config or CorrelatorConfig.default()
    events = events_from_records(records, cfg)
    clusters = cluster_failures(events, cfg)
    return AnalysisResult(events=events, clusters=clusters, warnings=[], inputs=["<memory>"], config=cfg.to_dict())


def analyze_paths(
    paths: Sequence[str],
    config: Optional[CorrelatorConfig] = None,
    *,
    config_path: Optional[str] = None,
    overrides: Optional[Mapping[str, object]] = None,
) -> AnalysisResult:
    """Read input files/directories, normalize failures, and return correlated clusters."""

    cfg = config or load_config(config_path, overrides)
    events, warnings, inputs = parse_paths(paths, cfg)
    clusters = cluster_failures(events, cfg)
    return AnalysisResult(events=events, clusters=clusters, warnings=warnings, inputs=inputs, config=cfg.to_dict())
