"""Rule-based root-cause tagging and remediation hints."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from .config import CorrelatorConfig


def _compile_all(patterns: Sequence[str]) -> Tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


@dataclass(frozen=True)
class RootCauseRule:
    label: str
    patterns: Tuple[re.Pattern[str], ...]
    action: str

    def matches(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.patterns)


RULES: Tuple[RootCauseRule, ...] = (
    RootCauseRule(
        "python-import",
        _compile_all(
            [
                r"\bmodulenotfounderror\b",
                r"\bimporterror\b",
                r"\bno module named\b",
                r"\bcannot import name\b",
            ]
        ),
        "Check Python dependencies, editable installs, package names, and PYTHONPATH/package discovery.",
    ),
    RootCauseRule(
        "javascript-dependency",
        _compile_all(
            [
                r"\bcannot find module\b",
                r"\bmodule not found\b",
                r"\bnpm err\b",
                r"\bpnpm\b.*\bfailed\b",
                r"\byarn\b.*\bfailed\b",
            ]
        ),
        "Refresh JavaScript dependencies, lockfiles, package manager version, and workspace install order.",
    ),
    RootCauseRule(
        "test-assertion",
        _compile_all(
            [
                r"\bassertionerror\b",
                r"\bassert\b.*\bfailed\b",
                r"\bexpected\b.*\b(actual|received|got)\b",
                r"\bpytest\b.*\bfailed\b",
                r"\btests? failed\b",
            ]
        ),
        "Inspect the failing assertion and compare recent behavior or fixture changes across affected repositories.",
    ),
    RootCauseRule(
        "timeout",
        _compile_all(
            [
                r"\btimeout\b",
                r"\btimed out\b",
                r"\bdeadline exceeded\b",
                r"\bexceeded.*minutes\b",
            ]
        ),
        "Look for slow external services, deadlocks, retries, and CI time limits before increasing timeouts.",
    ),
    RootCauseRule(
        "network",
        _compile_all(
            [
                r"\beconnreset\b",
                r"\benotfound\b",
                r"\bconnection refused\b",
                r"\bnetwork is unreachable\b",
                r"\btemporary failure in name resolution\b",
                r"\bssl\b.*\bcertificate\b",
            ]
        ),
        "Check registry/service availability, DNS, proxy settings, certificates, and retry/backoff policy.",
    ),
    RootCauseRule(
        "dependency-version",
        _compile_all(
            [
                r"\bversion conflict\b",
                r"\bincompatible\b",
                r"\brequires python\b",
                r"\bpeer dep",
                r"\blockfile\b.*\bout.?of.?date\b",
                r"\bresolution impossible\b",
            ]
        ),
        "Compare dependency constraints and lockfiles; pin, upgrade, or align the shared transitive dependency.",
    ),
    RootCauseRule(
        "auth-permission",
        _compile_all(
            [
                r"\bunauthorized\b",
                r"\bforbidden\b",
                r"\bbad credentials\b",
                r"\bpermission denied\b",
                r"\baccess denied\b",
                r"\bresource not accessible by integration\b",
            ]
        ),
        "Validate CI permissions, scoped tokens, GitHub Actions permissions blocks, and secret availability.",
    ),
    RootCauseRule(
        "lint-style",
        _compile_all(
            [
                r"\bflake8\b",
                r"\bruff\b",
                r"\beslint\b",
                r"\bprettier\b",
                r"\bblack\b.*\bwould reformat\b",
                r"\blint\b.*\bfailed\b",
            ]
        ),
        "Run the formatter/linter locally and check whether shared lint configuration recently changed.",
    ),
    RootCauseRule(
        "type-check",
        _compile_all(
            [
                r"\bmypy\b",
                r"\bpyright\b",
                r"\btsc\b",
                r"\btypescript\b.*\berror\b",
                r"\btype\b.*\bis not assignable\b",
            ]
        ),
        "Review type definitions, generated clients, and dependency type-package versions.",
    ),
    RootCauseRule(
        "build-tooling",
        _compile_all(
            [
                r"\bmake\b.*\berror\b",
                r"\bcmake\b",
                r"\bcompilation terminated\b",
                r"\bgcc\b.*\berror\b",
                r"\bwebpack\b.*\bfailed\b",
                r"\bbuild failed\b",
            ]
        ),
        "Inspect compiler/build-tool versions, generated files, and platform-specific build flags.",
    ),
    RootCauseRule(
        "runner-environment",
        _compile_all(
            [
                r"\bubuntu-latest\b",
                r"\bwindows-latest\b",
                r"\bmacos-latest\b",
                r"\bimageos\b",
                r"\bhosted agent\b",
                r"\bthe hosted runner\b",
            ]
        ),
        "Check GitHub runner image updates and pin runner images or tool versions where needed.",
    ),
    RootCauseRule(
        "container",
        _compile_all(
            [
                r"\bdocker\b",
                r"\bcontainer\b",
                r"\bno space left on device\b",
                r"\bfailed to solve\b",
                r"\bmanifest unknown\b",
            ]
        ),
        "Check image tags, registry access, disk pressure, and Dockerfile cache assumptions.",
    ),
)

DEFAULT_ACTIONS: Dict[str, List[str]] = {
    rule.label: [rule.action] for rule in RULES
}


def detect_root_causes(text: str, tokens: Sequence[str], config: CorrelatorConfig) -> Tuple[List[str], List[str]]:
    """Return labels and rule-hit names for a normalized failure."""

    haystack = text.lower() + " " + " ".join(tokens).lower()
    labels: List[str] = []
    hits: List[str] = []
    for rule in RULES:
        if rule.matches(haystack):
            labels.append(rule.label)
            hits.append(rule.label)
    for label, patterns in config.custom_root_causes.items():
        for pattern in patterns:
            try:
                if re.search(pattern, haystack, flags=re.IGNORECASE):
                    labels.append(label)
                    hits.append(f"custom:{label}")
                    break
            except re.error:
                continue
    if not labels:
        labels.append("unknown")
    return _dedupe(labels), _dedupe(hits)


def infer_language(text: str, labels: Iterable[str]) -> str:
    joined_labels = set(labels)
    haystack = text.lower()
    if "python-import" in joined_labels or "pytest" in haystack or "modulenotfounderror" in haystack:
        return "python"
    if "javascript-dependency" in joined_labels or "npm" in haystack or "node" in haystack or "typescript" in haystack:
        return "javascript"
    if "container" in joined_labels or "docker" in haystack:
        return "container"
    if "build-tooling" in joined_labels and any(term in haystack for term in ["gcc", "cmake", "make"]):
        return "native"
    return "unknown"


def severity_from_labels(labels: Sequence[str], text: str) -> str:
    label_set = set(labels)
    if {"auth-permission", "network", "container"} & label_set:
        return "critical"
    if {"dependency-version", "python-import", "javascript-dependency", "build-tooling"} & label_set:
        return "error"
    if "lint-style" in label_set or "deprecation" in text.lower():
        return "warning"
    return "error"


def suggested_actions(labels: Sequence[str], config: CorrelatorConfig) -> List[str]:
    actions: List[str] = []
    merged = dict(DEFAULT_ACTIONS)
    for label, label_actions in config.label_actions.items():
        merged[label] = list(label_actions)
    for label in labels:
        actions.extend(merged.get(label, []))
    if not actions:
        actions.append("Open the representative log, compare repeated tokens, and assign an owner for the shared failure mode.")
    return _dedupe(actions)


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
