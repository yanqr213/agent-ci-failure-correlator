"""Log summarization, normalization, and tokenization."""

from __future__ import annotations

import re
from hashlib import sha1
from typing import Iterable, List, Sequence, Tuple

from .config import CorrelatorConfig


ERROR_LINE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(error|failed|failure|fatal|exception|traceback|assertionerror)\b",
        r"\b(module not found|cannot find module|no module named)\b",
        r"\b(timeout|timed out|deadline exceeded)\b",
        r"\b(exit code|errno|segmentation fault)\b",
        r"\b(econnreset|enotfound|network is unreachable|connection refused)\b",
        r"\b(deprecated|deprecationwarning)\b",
    ]
]

NOISE_PATTERNS = [
    re.compile(r"\x1b\[[0-9;]*m"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b"),
    re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b"),
    re.compile(r"https?://\S+"),
    re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])(?:[A-Z]:)?[/\\][^\s:]+"),
    re.compile(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?\b"),
    re.compile(r"\b\d+(?:\.\d+)?s\b"),
    re.compile(r"\b\d+(?:\.\d+)?ms\b"),
    re.compile(r"\b\d{2,}\b"),
]

TOKEN_RE = re.compile(r"[a-z][a-z0-9_.+-]{1,}")


def summarize_log(text: str, config: CorrelatorConfig) -> str:
    """Extract the most useful error-bearing lines from a log fragment."""

    clean_lines = _clean_lines(text, config)
    if not clean_lines:
        return ""
    candidate_indexes = []
    for index, line in enumerate(clean_lines):
        if any(pattern.search(line) for pattern in ERROR_LINE_PATTERNS):
            candidate_indexes.append(index)
    if not candidate_indexes:
        selected = clean_lines[: config.max_summary_lines]
    else:
        indexes = set()
        for index in candidate_indexes:
            start = max(0, index - config.context_lines)
            end = min(len(clean_lines), index + config.context_lines + 1)
            indexes.update(range(start, end))
        selected = [clean_lines[index] for index in sorted(indexes)]
    summary = "\n".join(selected[: config.max_summary_lines]).strip()
    if len(summary) > config.max_summary_chars:
        return summary[: config.max_summary_chars].rstrip() + "\n..."
    return summary


def normalize_text(text: str) -> str:
    """Remove volatile details and lower-case a failure signature."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in NOISE_PATTERNS:
        normalized = pattern.sub(_noise_replacement(pattern), normalized)
    normalized = re.sub(r"['\"]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\bline <num>\b", "line <num>", normalized, flags=re.IGNORECASE)
    return normalized.strip().lower()


def tokenize(normalized_text: str, stop_words: Sequence[str]) -> List[str]:
    stop = set(stop_words)
    seen = set()
    tokens: List[str] = []
    for term in TOKEN_RE.findall(normalized_text.lower()):
        term = term.strip("._+-")
        if len(term) < 2 or term in stop or term.startswith("num"):
            continue
        if term not in seen:
            seen.add(term)
            tokens.append(term)
    return tokens


def stable_event_id(parts: Iterable[str]) -> str:
    digest = sha1("\x1f".join(parts).encode("utf-8", errors="ignore")).hexdigest()
    return digest[:16]


def canonical_repository(name: str, aliases: dict[str, str]) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        return "unknown"
    return aliases.get(cleaned, aliases.get(cleaned.lower(), cleaned))


def summarize_and_normalize(text: str, config: CorrelatorConfig) -> Tuple[str, str, List[str]]:
    summary = summarize_log(text, config)
    normalized = normalize_text(summary or text)
    tokens = tokenize(normalized, config.stop_words)
    return summary, normalized, tokens


def _clean_lines(text: str, config: CorrelatorConfig) -> List[str]:
    ignore = [re.compile(pattern) for pattern in config.ignore_patterns]
    clean = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = NOISE_PATTERNS[0].sub("", line).strip()
        if not line:
            continue
        if any(pattern.search(line) for pattern in ignore):
            continue
        clean.append(line)
    return clean


def _noise_replacement(pattern: re.Pattern[str]) -> str:
    source = pattern.pattern
    if "http" in source:
        return "<url>"
    if "A-Z" in source or "[^\\s:]" in source:
        return "<path>"
    if "0-9a-f" in source:
        return "<hash>"
    if "\\d+\\.\\d+\\.\\d+" in source:
        return "<version>"
    if "\\d{4}" in source or "\\d{2}" in source:
        return "<time>"
    return "<num>"
