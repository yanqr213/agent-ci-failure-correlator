"""Markdown and JSON report generation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import __version__
from .models import AnalysisResult, FailureEvent, RootCauseCluster


def render_json(result: AnalysisResult, *, pretty: bool = True) -> str:
    indent = 2 if pretty else None
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=indent, sort_keys=False) + "\n"


def render_markdown(result: AnalysisResult) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    data = result.to_dict()
    lines: List[str] = [
        "# CI Failure Correlation Report",
        "",
        f"- Generated: {now}",
        f"- Inputs: {data['summary']['input_count']}",
        f"- Failure events: {data['summary']['event_count']}",
        f"- Root-cause clusters: {data['summary']['cluster_count']}",
        f"- Cross-repository repeated clusters: {data['summary']['cross_repository_cluster_count']}",
        "",
    ]
    if result.warnings:
        lines.extend(["## Warnings", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    if not result.clusters:
        lines.extend(["## Result", "", "No CI failures were detected in the provided inputs.", ""])
        return "\n".join(lines)

    cross_repo = [cluster for cluster in result.clusters if cluster.is_cross_repository]
    if cross_repo:
        lines.extend(["## Cross-Repository Repeats", ""])
        for cluster in cross_repo:
            lines.append(_cluster_one_liner(cluster))
        lines.append("")

    lines.extend(["## Clusters", ""])
    for cluster in result.clusters:
        lines.extend(_render_cluster(cluster))
        lines.append("")
    lines.extend(["## Inputs", ""])
    for input_path in result.inputs:
        lines.append(f"- `{input_path}`")
    lines.append("")
    return "\n".join(lines)


def render_brief(result: AnalysisResult) -> str:
    data = result.to_dict()
    summary = data["summary"]
    decision = _brief_decision(result)
    lines: List[str] = [
        "# CI Failure Triage Brief",
        "",
        f"Decision: {decision}",
        (
            "Scope: "
            f"{summary['event_count']} failure events, "
            f"{summary['cluster_count']} clusters, "
            f"{summary['cross_repository_cluster_count']} cross-repository repeats."
        ),
        "",
        "Top repeated causes:",
    ]
    top_clusters = sorted(result.clusters, key=_brief_cluster_rank, reverse=True)[:5]
    if not top_clusters:
        lines.append("- No CI failure clusters were detected.")
    for cluster in top_clusters:
        label = ", ".join(cluster.root_cause_labels) or "unknown"
        repos = ", ".join(cluster.repositories[:4]) or "unknown repository"
        if len(cluster.repositories) > 4:
            repos += f", +{len(cluster.repositories) - 4} more"
        repeat = "cross-repo" if cluster.is_cross_repository else "single-repo"
        lines.append(
            f"- {cluster.cluster_id} [{repeat}] {label}: "
            f"{cluster.event_count} events across {cluster.repository_count} repositories "
            f"({repos}), confidence {cluster.confidence:.2f}."
        )
        for action in cluster.suggested_actions[:2]:
            lines.append(f"  Next: {action}")
        links = [event.source.url for event in cluster.events if event.source.url]
        if links:
            lines.append(f"  Runs: {', '.join(links[:3])}")
    lines.extend(["", "Agent handoff:"])
    if result.has_cross_repository_repeats:
        lines.append("- Fix cross-repository repeated clusters first; one shared dependency or runner change may clear multiple emails.")
    elif result.has_failures:
        lines.append("- Start with the highest-confidence cluster and verify whether the suggested action reproduces locally.")
    else:
        lines.append("- No failure action is required for the provided inputs.")
    if result.warnings:
        lines.append(f"- Review parser warnings before acting: {len(result.warnings)} warning(s).")
    lines.append("")
    return "\n".join(lines)


def render_sarif(result: AnalysisResult) -> str:
    rules = _sarif_rules(result.clusters)
    sarif = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "agent-ci-failure-correlator",
                        "semanticVersion": __version__,
                        "informationUri": "https://github.com/yanqr213/agent-ci-failure-correlator",
                        "rules": rules,
                    }
                },
                "automationDetails": {"id": "ci-failure-correlation"},
                "results": [_cluster_to_sarif(cluster) for cluster in result.clusters],
                "properties": {
                    "summary": result.to_dict()["summary"],
                    "inputs": result.inputs,
                    "warnings": result.warnings,
                },
            }
        ],
    }
    return json.dumps(sarif, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _cluster_one_liner(cluster: RootCauseCluster) -> str:
    labels = ", ".join(cluster.root_cause_labels)
    repos = ", ".join(cluster.repositories)
    return (
        f"- **{cluster.cluster_id}** `{labels}`: {cluster.event_count} events across "
        f"{cluster.repository_count} repositories ({repos}), confidence {cluster.confidence:.2f}"
    )


def _render_cluster(cluster: RootCauseCluster) -> List[str]:
    labels = ", ".join(f"`{label}`" for label in cluster.root_cause_labels)
    lines = [
        f"### {cluster.cluster_id}: {cluster.root_cause_labels[0]}",
        "",
        f"- Events: {cluster.event_count}",
        f"- Repositories: {cluster.repository_count} ({', '.join(cluster.repositories)})",
        f"- Severity: `{cluster.severity}`",
        f"- Confidence: `{cluster.confidence:.2f}`",
        f"- Labels: {labels}",
        f"- Signature: `{cluster.normalized_signature}`",
        "",
        "**Representative summary**",
        "",
        "```text",
        _trim_block(cluster.representative_summary, 1800),
        "```",
        "",
        "**Suggested actions**",
        "",
    ]
    for action in cluster.suggested_actions:
        lines.append(f"- {action}")
    evidence = cluster.similarity_evidence
    if evidence:
        lines.extend(
            [
                "",
                "**Evidence**",
                "",
                f"- Average pair similarity: `{evidence.get('average_pair_similarity')}`",
                f"- Minimum pair similarity: `{evidence.get('min_pair_similarity')}`",
                f"- Shared tokens: {', '.join('`' + token + '`' for token in evidence.get('shared_tokens', [])[:12]) or 'none'}",
            ]
        )
    lines.extend(["", "**Events**", ""])
    for event in cluster.events:
        lines.extend(_render_event(event))
    return lines


def _render_event(event: FailureEvent) -> List[str]:
    source = event.source
    location_bits = [
        source.repository,
        source.workflow,
        source.job_name,
        source.step_name,
    ]
    location = " / ".join(bit for bit in location_bits if bit)
    lines = [f"- `{event.event_id}` {location or source.path}"]
    details = []
    if source.run_id:
        details.append(f"run `{source.run_id}`")
    if event.command:
        details.append(f"command `{event.command}`")
    if event.exit_code is not None:
        details.append(f"exit `{event.exit_code}`")
    if source.url:
        details.append(f"[run link]({source.url})")
    if details:
        lines.append(f"  - {'; '.join(details)}")
    lines.append(f"  - Source: `{source.path}`")
    return lines


def _trim_block(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.strip()
    return text[:limit].rstrip() + "\n..."


def _brief_decision(result: AnalysisResult) -> str:
    if result.has_cross_repository_repeats:
        return "BATCH-FIX - repeated failures span repositories."
    if result.has_failures:
        return "INVESTIGATE - failures found but no cross-repository repeat was detected."
    return "CLEAR - no failure events were detected."


def _brief_cluster_rank(cluster: RootCauseCluster) -> tuple[int, int, float, int]:
    return (
        1 if cluster.is_cross_repository else 0,
        cluster.repository_count,
        cluster.confidence,
        cluster.event_count,
    )


def _sarif_rules(clusters: List[RootCauseCluster]) -> List[Dict[str, Any]]:
    labels = sorted({label for cluster in clusters for label in cluster.root_cause_labels})
    return [
        {
            "id": _sarif_rule_id(label),
            "name": label.replace("-", " ").title(),
            "shortDescription": {"text": f"CI failures clustered as {label}."},
            "fullDescription": {"text": _sarif_help(label)},
            "defaultConfiguration": {"level": "warning"},
            "help": {"text": _sarif_help(label), "markdown": _sarif_help(label)},
            "properties": {
                "precision": "medium",
                "tags": ["ci", "github-actions", "agent-handoff", "failure-correlation", label],
            },
        }
        for label in labels
    ]


def _cluster_to_sarif(cluster: RootCauseCluster) -> Dict[str, Any]:
    representative = cluster.events[0] if cluster.events else None
    label = cluster.root_cause_labels[0] if cluster.root_cause_labels else "unknown"
    message = (
        f"{cluster.cluster_id} {label}: {cluster.event_count} event(s) across "
        f"{cluster.repository_count} repository/repositories; confidence {cluster.confidence:.2f}. "
        f"{cluster.representative_summary}"
    )
    result = {
        "ruleId": _sarif_rule_id(label),
        "level": _sarif_level(cluster),
        "message": {"text": _trim_inline(message, 800)},
        "locations": [_sarif_location(cluster, representative)],
        "partialFingerprints": {
            "agentCiFailureCorrelator/v1": hashlib.sha256(cluster.normalized_signature.encode("utf-8")).hexdigest()[:32]
        },
        "properties": {
            "cluster_id": cluster.cluster_id,
            "confidence": cluster.confidence,
            "event_count": cluster.event_count,
            "repositories": cluster.repositories,
            "root_cause_labels": cluster.root_cause_labels,
            "suggested_actions": cluster.suggested_actions,
            "is_cross_repository": cluster.is_cross_repository,
            "events": [_event_reference(event) for event in cluster.events],
            "similarity_evidence": cluster.similarity_evidence,
        },
    }
    return result


def _sarif_location(cluster: RootCauseCluster, representative: Optional[FailureEvent]) -> Dict[str, Any]:
    uri = representative.source.path if representative else "ci-failure-input"
    logical_name = cluster.cluster_id
    if representative:
        bits = [representative.source.repository, representative.source.workflow, representative.source.job_name]
        logical_name = " / ".join(bit for bit in bits if bit) or cluster.cluster_id
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": (uri or "ci-failure-input").replace("\\", "/")},
            "region": {"startLine": 1},
        },
        "logicalLocations": [
            {
                "name": logical_name,
                "fullyQualifiedName": f"{cluster.cluster_id}.{cluster.normalized_signature}",
                "kind": "function",
            }
        ],
    }


def _event_reference(event: FailureEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "repository": event.source.repository,
        "workflow": event.source.workflow,
        "job_name": event.source.job_name,
        "run_id": event.source.run_id,
        "url": event.source.url,
        "source_path": event.source.path,
        "summary": event.summary,
    }


def _sarif_rule_id(label: str) -> str:
    return f"ci-failure.{label or 'unknown'}"


def _sarif_level(cluster: RootCauseCluster) -> str:
    if cluster.is_cross_repository or cluster.severity in {"critical", "error"}:
        return "error"
    if cluster.severity == "warning":
        return "warning"
    return "note"


def _sarif_help(label: str) -> str:
    return (
        f"Failures with the `{label}` root-cause label were clustered together. "
        "Review the representative summary, shared tokens, affected repositories, and suggested actions before assigning repair work to an agent."
    )


def _trim_inline(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "..."
