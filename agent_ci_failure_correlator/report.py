"""Markdown and JSON report generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List

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
