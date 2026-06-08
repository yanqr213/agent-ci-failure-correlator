"""Similarity clustering for normalized CI failures."""

from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from .config import CorrelatorConfig
from .models import FailureEvent, RootCauseCluster
from .normalizer import stable_event_id
from .rules import suggested_actions


def cluster_failures(events: Sequence[FailureEvent], config: CorrelatorConfig) -> List[RootCauseCluster]:
    """Cluster failures using rule labels, token overlap, and text similarity."""

    if not events:
        return []
    groups: List[List[FailureEvent]] = []
    for event in events:
        best_index = None
        best_score = 0.0
        for index, group in enumerate(groups):
            score = max(similarity(event, member) for member in group)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index is not None and best_score >= config.similarity_threshold:
            groups[best_index].append(event)
        else:
            groups.append([event])

    clusters = [_make_cluster(group, config, index) for index, group in enumerate(groups, start=1)]
    clusters = [cluster for cluster in clusters if cluster.event_count >= config.min_cluster_size]
    clusters.sort(
        key=lambda cluster: (
            not cluster.is_cross_repository,
            -cluster.event_count,
            -cluster.confidence,
            cluster.representative_summary,
        )
    )
    for index, cluster in enumerate(clusters, start=1):
        cluster.cluster_id = f"C{index:03d}"
    return clusters


def similarity(left: FailureEvent, right: FailureEvent) -> float:
    """Score whether two events likely share one root cause."""

    token_score = jaccard(set(left.tokens), set(right.tokens))
    text_score = SequenceMatcher(None, left.normalized_text, right.normalized_text).ratio()
    label_score = label_similarity(left.root_cause_labels, right.root_cause_labels)
    command_score = 1.0 if left.command and right.command and left.command == right.command else 0.0
    language_score = 1.0 if left.language == right.language and left.language != "unknown" else 0.0
    weighted = (
        token_score * 0.38
        + text_score * 0.30
        + label_score * 0.22
        + command_score * 0.05
        + language_score * 0.05
    )
    if label_score >= 1.0 and token_score >= 0.34:
        weighted += 0.08
    if left.normalized_text and right.normalized_text and _prefix_signature(left.normalized_text) == _prefix_signature(right.normalized_text):
        weighted += 0.05
    return min(1.0, weighted)


def jaccard(left: Set[str], right: Set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def label_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    if left_set == {"unknown"} and right_set == {"unknown"}:
        return 0.1
    return jaccard(left_set, right_set)


def _make_cluster(group: List[FailureEvent], config: CorrelatorConfig, index: int) -> RootCauseCluster:
    repositories = sorted({event.source.repository or "unknown" for event in group})
    labels = _ranked_labels(group)
    representative = _representative_event(group)
    pair_scores = _pair_scores(group)
    confidence = _confidence(group, pair_scores, labels)
    normalized_signature = _signature(group, labels)
    return RootCauseCluster(
        cluster_id=f"C{index:03d}",
        root_cause_labels=labels,
        confidence=confidence,
        severity=_cluster_severity(group),
        representative_summary=representative.summary,
        normalized_signature=normalized_signature,
        events=sorted(group, key=lambda event: (event.source.repository, event.source.workflow, event.source.job_name, event.event_id)),
        repositories=repositories,
        suggested_actions=suggested_actions(labels, config),
        similarity_evidence={
            "average_pair_similarity": round(sum(pair_scores) / len(pair_scores), 3) if pair_scores else 1.0,
            "min_pair_similarity": round(min(pair_scores), 3) if pair_scores else 1.0,
            "shared_tokens": _shared_tokens(group),
            "shared_labels": labels,
        },
    )


def _ranked_labels(events: Sequence[FailureEvent]) -> List[str]:
    counts: Counter[str] = Counter()
    for event in events:
        counts.update(event.root_cause_labels)
    labels = [label for label, _count in counts.most_common() if label != "unknown"]
    if labels:
        return labels
    return ["unknown"]


def _representative_event(events: Sequence[FailureEvent]) -> FailureEvent:
    if len(events) == 1:
        return events[0]
    token_counts: Counter[str] = Counter()
    for event in events:
        token_counts.update(event.tokens)

    def score(event: FailureEvent) -> Tuple[int, int, int]:
        return (
            sum(token_counts[token] for token in event.tokens),
            len(event.rule_hits),
            -len(event.summary),
        )

    return max(events, key=score)


def _pair_scores(events: Sequence[FailureEvent]) -> List[float]:
    scores: List[float] = []
    for index, left in enumerate(events):
        for right in events[index + 1 :]:
            scores.append(similarity(left, right))
    return scores


def _confidence(events: Sequence[FailureEvent], pair_scores: Sequence[float], labels: Sequence[str]) -> float:
    if not events:
        return 0.0
    average = sum(pair_scores) / len(pair_scores) if pair_scores else 0.68
    repo_bonus = min(0.12, max(0, len({event.source.repository for event in events}) - 1) * 0.04)
    size_bonus = min(0.12, (len(events) - 1) * 0.03)
    label_bonus = 0.08 if labels and labels != ["unknown"] else -0.05
    score = average * 0.72 + repo_bonus + size_bonus + label_bonus
    return round(max(0.1, min(0.99, score)), 3)


def _signature(events: Sequence[FailureEvent], labels: Sequence[str]) -> str:
    shared = _shared_tokens(events)[:8]
    seed = " ".join(labels + shared) or events[0].normalized_text[:80]
    digest = stable_event_id([seed])
    return f"{'-'.join(labels[:2])}:{digest}"


def _shared_tokens(events: Sequence[FailureEvent]) -> List[str]:
    if not events:
        return []
    counts: Dict[str, int] = defaultdict(int)
    for event in events:
        for token in set(event.tokens):
            counts[token] += 1
    minimum = len(events) if len(events) <= 2 else max(2, len(events) - 1)
    ranked = sorted(
        [token for token, count in counts.items() if count >= minimum],
        key=lambda token: (-counts[token], token),
    )
    if ranked:
        return ranked
    counter: Counter[str] = Counter()
    for event in events:
        counter.update(event.tokens)
    return [token for token, _count in counter.most_common(8)]


def _cluster_severity(events: Sequence[FailureEvent]) -> str:
    order = {"critical": 3, "error": 2, "warning": 1, "info": 0}
    return max((event.severity for event in events), key=lambda severity: order.get(severity, 0))


def _prefix_signature(text: str) -> str:
    return " ".join(text.split()[:8])
