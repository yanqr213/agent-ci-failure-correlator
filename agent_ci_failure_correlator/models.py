"""Public data models for CI failure correlation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class SourceRef:
    """Where a failure event came from."""

    path: str
    format: str = "unknown"
    repository: str = ""
    workflow: str = ""
    run_id: str = ""
    job_name: str = ""
    step_name: str = ""
    url: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, path: str = "") -> "SourceRef":
        return cls(
            path=str(data.get("path") or path),
            format=str(data.get("format") or "normalized"),
            repository=str(data.get("repository") or data.get("repo") or ""),
            workflow=str(data.get("workflow") or ""),
            run_id=str(data.get("run_id") or data.get("runId") or data.get("id") or ""),
            job_name=str(data.get("job_name") or data.get("job") or ""),
            step_name=str(data.get("step_name") or data.get("step") or ""),
            url=str(data.get("url") or data.get("html_url") or ""),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "path": self.path,
            "format": self.format,
            "repository": self.repository,
            "workflow": self.workflow,
            "run_id": self.run_id,
            "job_name": self.job_name,
            "step_name": self.step_name,
            "url": self.url,
        }


@dataclass
class FailureEvent:
    """A normalized CI failure from a run, job, test case, or log fragment."""

    event_id: str
    source: SourceRef
    raw_text: str
    summary: str
    normalized_text: str
    tokens: List[str]
    root_cause_labels: List[str]
    rule_hits: List[str]
    severity: str = "error"
    language: str = "unknown"
    command: str = ""
    exit_code: Optional[int] = None
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source": self.source.to_dict(),
            "raw_text": self.raw_text,
            "summary": self.summary,
            "normalized_text": self.normalized_text,
            "tokens": list(self.tokens),
            "root_cause_labels": list(self.root_cause_labels),
            "rule_hits": list(self.rule_hits),
            "severity": self.severity,
            "language": self.language,
            "command": self.command,
            "exit_code": self.exit_code,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FailureEvent":
        source_data = data.get("source")
        if isinstance(source_data, Mapping):
            source = SourceRef.from_mapping(source_data)
        else:
            source = SourceRef(path=str(data.get("path") or ""), format="normalized")
        return cls(
            event_id=str(data.get("event_id") or data.get("id") or ""),
            source=source,
            raw_text=str(data.get("raw_text") or data.get("log") or data.get("text") or ""),
            summary=str(data.get("summary") or ""),
            normalized_text=str(data.get("normalized_text") or ""),
            tokens=[str(token) for token in data.get("tokens", [])],
            root_cause_labels=[str(label) for label in data.get("root_cause_labels", [])],
            rule_hits=[str(hit) for hit in data.get("rule_hits", [])],
            severity=str(data.get("severity") or "error"),
            language=str(data.get("language") or "unknown"),
            command=str(data.get("command") or ""),
            exit_code=data.get("exit_code") if isinstance(data.get("exit_code"), int) else None,
            timestamp=str(data.get("timestamp") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class RootCauseCluster:
    """A group of failures believed to share the same cause."""

    cluster_id: str
    root_cause_labels: List[str]
    confidence: float
    severity: str
    representative_summary: str
    normalized_signature: str
    events: List[FailureEvent]
    repositories: List[str]
    suggested_actions: List[str]
    similarity_evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def repository_count(self) -> int:
        return len(self.repositories)

    @property
    def is_cross_repository(self) -> bool:
        return self.repository_count > 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "root_cause_labels": list(self.root_cause_labels),
            "confidence": self.confidence,
            "severity": self.severity,
            "representative_summary": self.representative_summary,
            "normalized_signature": self.normalized_signature,
            "event_count": self.event_count,
            "repositories": list(self.repositories),
            "repository_count": self.repository_count,
            "is_cross_repository": self.is_cross_repository,
            "suggested_actions": list(self.suggested_actions),
            "similarity_evidence": dict(self.similarity_evidence),
            "events": [event.to_dict() for event in self.events],
        }


@dataclass
class AnalysisResult:
    """Complete result returned by the public API and CLI."""

    events: List[FailureEvent]
    clusters: List[RootCauseCluster]
    warnings: List[str]
    inputs: List[str]
    config: Dict[str, Any]

    @property
    def has_failures(self) -> bool:
        return bool(self.events)

    @property
    def has_cross_repository_repeats(self) -> bool:
        return any(cluster.is_cross_repository for cluster in self.clusters)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "input_count": len(self.inputs),
                "event_count": len(self.events),
                "cluster_count": len(self.clusters),
                "cross_repository_cluster_count": sum(1 for cluster in self.clusters if cluster.is_cross_repository),
                "has_failures": self.has_failures,
                "has_cross_repository_repeats": self.has_cross_repository_repeats,
            },
            "inputs": list(self.inputs),
            "warnings": list(self.warnings),
            "config": dict(self.config),
            "clusters": [cluster.to_dict() for cluster in self.clusters],
            "events": [event.to_dict() for event in self.events],
        }
