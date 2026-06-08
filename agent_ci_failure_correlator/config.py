"""Configuration loading and defaults."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union


DEFAULT_IGNORE_PATTERNS = [
    r"^\s*$",
    r"^Run\s+",
    r"^shell:\s+",
    r"^env:\s+",
    r"^##\[group\]",
    r"^##\[endgroup\]",
    r"^\[command\]",
]

DEFAULT_STOP_WORDS = [
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "ci",
    "for",
    "from",
    "in",
    "is",
    "it",
    "job",
    "log",
    "of",
    "on",
    "or",
    "run",
    "step",
    "the",
    "to",
    "with",
]


@dataclass(frozen=True)
class CorrelatorConfig:
    """Tunable options for normalization, clustering, and reports."""

    similarity_threshold: float = 0.58
    min_cluster_size: int = 1
    max_summary_lines: int = 8
    max_summary_chars: int = 4000
    context_lines: int = 2
    fail_on_cross_repo: bool = False
    fail_on_any_failure: bool = False
    include_raw_events: bool = True
    repository_aliases: Dict[str, str] = field(default_factory=dict)
    ignore_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    stop_words: List[str] = field(default_factory=lambda: list(DEFAULT_STOP_WORDS))
    custom_root_causes: Dict[str, List[str]] = field(default_factory=dict)
    label_actions: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "CorrelatorConfig":
        return cls()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CorrelatorConfig":
        defaults = cls.default()
        merged = defaults.to_dict()
        for key, value in data.items():
            if key in merged:
                merged[key] = value
        return cls(
            similarity_threshold=float(merged["similarity_threshold"]),
            min_cluster_size=int(merged["min_cluster_size"]),
            max_summary_lines=int(merged["max_summary_lines"]),
            max_summary_chars=int(merged["max_summary_chars"]),
            context_lines=int(merged["context_lines"]),
            fail_on_cross_repo=bool(merged["fail_on_cross_repo"]),
            fail_on_any_failure=bool(merged["fail_on_any_failure"]),
            include_raw_events=bool(merged["include_raw_events"]),
            repository_aliases=_dict_of_strings(merged["repository_aliases"]),
            ignore_patterns=_list_of_strings(merged["ignore_patterns"]),
            stop_words=_list_of_strings(merged["stop_words"]),
            custom_root_causes=_dict_of_string_lists(merged["custom_root_causes"]),
            label_actions=_dict_of_string_lists(merged["label_actions"]),
        )

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "CorrelatorConfig":
        config_path = Path(path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError(f"Config must be a JSON object: {config_path}")
        return cls.from_mapping(data)

    @classmethod
    def merge(cls, base: Optional["CorrelatorConfig"], override: Mapping[str, Any]) -> "CorrelatorConfig":
        base_config = base or cls.default()
        data = base_config.to_dict()
        data.update(dict(override))
        return cls.from_mapping(data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similarity_threshold": self.similarity_threshold,
            "min_cluster_size": self.min_cluster_size,
            "max_summary_lines": self.max_summary_lines,
            "max_summary_chars": self.max_summary_chars,
            "context_lines": self.context_lines,
            "fail_on_cross_repo": self.fail_on_cross_repo,
            "fail_on_any_failure": self.fail_on_any_failure,
            "include_raw_events": self.include_raw_events,
            "repository_aliases": dict(self.repository_aliases),
            "ignore_patterns": list(self.ignore_patterns),
            "stop_words": list(self.stop_words),
            "custom_root_causes": {key: list(value) for key, value in self.custom_root_causes.items()},
            "label_actions": {key: list(value) for key, value in self.label_actions.items()},
        }


def load_config(config_path: Optional[Union[str, Path]] = None, overrides: Optional[Mapping[str, Any]] = None) -> CorrelatorConfig:
    config = CorrelatorConfig.from_file(config_path) if config_path else CorrelatorConfig.default()
    if overrides:
        config = CorrelatorConfig.merge(config, overrides)
    return config


def _list_of_strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return [str(value)]


def _dict_of_strings(value: Any) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _dict_of_string_lists(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _list_of_strings(item) for key, item in value.items()}
