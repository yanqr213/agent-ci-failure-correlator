"""Input parsers for workflow JSON, normalized exports, JSONL, JUnit XML, and logs."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .config import CorrelatorConfig
from .models import FailureEvent, SourceRef
from .normalizer import canonical_repository, stable_event_id, summarize_and_normalize
from .rules import detect_root_causes, infer_language, severity_from_labels


TEXT_EXTENSIONS = {".log", ".txt", ".out", ".err", ".md"}
JSON_EXTENSIONS = {".json", ".jsonl", ".ndjson"}
XML_EXTENSIONS = {".xml"}


def discover_input_files(paths: Sequence[str]) -> List[Path]:
    files: List[Path] = []
    for item in paths:
        path = Path(item)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and _is_supported(child):
                    files.append(child)
        elif path.is_file() and _is_supported(path):
            files.append(path)
    return _dedupe_paths(files)


def parse_paths(paths: Sequence[str], config: CorrelatorConfig) -> Tuple[List[FailureEvent], List[str], List[str]]:
    warnings: List[str] = []
    files = discover_input_files(paths)
    events: List[FailureEvent] = []
    for file_path in files:
        try:
            events.extend(parse_file(file_path, config))
        except Exception as exc:  # pragma: no cover - defensive, covered through CLI behavior
            warnings.append(f"Skipped {file_path}: {exc}")
    return events, warnings, [str(path) for path in files]


def parse_file(path: Path, config: CorrelatorConfig) -> List[FailureEvent]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl" or suffix == ".ndjson":
        return _parse_jsonl(path, config)
    if suffix == ".json":
        return _parse_json_file(path, config)
    if suffix in XML_EXTENSIONS:
        return _parse_junit_xml(path, config)
    return [_event_from_text(path.read_text(encoding="utf-8", errors="replace"), path, config, SourceRef(path=str(path), format="log"))]


def events_from_records(records: Iterable[Mapping[str, Any]], config: CorrelatorConfig, *, path: str = "<memory>") -> List[FailureEvent]:
    events: List[FailureEvent] = []
    for index, record in enumerate(records):
        events.extend(_events_from_mapping(record, Path(path), config, fallback_index=index))
    return events


def _parse_jsonl(path: Path, config: CorrelatorConfig) -> List[FailureEvent]:
    events: List[FailureEvent] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, Mapping):
            events.extend(_events_from_mapping(data, path, config, fallback_index=index))
    return events


def _parse_json_file(path: Path, config: CorrelatorConfig) -> List[FailureEvent]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(data, list):
        events: List[FailureEvent] = []
        for index, item in enumerate(data):
            if isinstance(item, Mapping):
                events.extend(_events_from_mapping(item, path, config, fallback_index=index))
        return events
    if isinstance(data, Mapping):
        if "clusters" in data and "events" in data and isinstance(data.get("events"), list):
            return [FailureEvent.from_dict(item) for item in data["events"] if isinstance(item, Mapping)]
        return _events_from_mapping(data, path, config, fallback_index=0)
    return []


def _parse_junit_xml(path: Path, config: CorrelatorConfig) -> List[FailureEvent]:
    tree = ET.parse(str(path))
    root = tree.getroot()
    repository = _attr(root, "repository") or _guess_repo_from_path(path)
    workflow = _attr(root, "workflow") or "junit"
    events: List[FailureEvent] = []
    for case in root.iter():
        if _strip_namespace(case.tag) != "testcase":
            continue
        failures = [child for child in list(case) if _strip_namespace(child.tag) in {"failure", "error"}]
        if not failures:
            continue
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "")
        for failure in failures:
            message = failure.attrib.get("message", "")
            body = failure.text or ""
            text = "\n".join(part for part in [classname, name, message, body] if part)
            source = SourceRef(
                path=str(path),
                format="junit-xml",
                repository=canonical_repository(repository, config.repository_aliases),
                workflow=workflow,
                job_name=classname,
                step_name=name,
            )
            events.append(_event_from_text(text, path, config, source, metadata={"testcase": name, "classname": classname}))
    return events


def _events_from_mapping(data: Mapping[str, Any], path: Path, config: CorrelatorConfig, *, fallback_index: int) -> List[FailureEvent]:
    if _looks_like_failure_event(data):
        event = FailureEvent.from_dict(data)
        if not event.event_id:
            event.event_id = stable_event_id([str(path), str(fallback_index), event.summary, event.normalized_text])
        return [event]

    repository = _repository_from_mapping(data, path, config)
    workflow = str(data.get("workflow") or data.get("workflow_name") or data.get("name") or "")
    run_id = str(data.get("run_id") or data.get("runId") or data.get("id") or data.get("databaseId") or "")
    url = str(data.get("url") or data.get("html_url") or data.get("web_url") or "")
    timestamp = str(data.get("created_at") or data.get("updated_at") or data.get("completed_at") or data.get("timestamp") or "")

    if _is_job(data):
        return [_event_from_job(data, path, config, repository, workflow, run_id, url, timestamp)]
    if _is_workflow_run(data):
        return _events_from_workflow_run(data, path, config, repository, workflow, run_id, url, timestamp)

    text = _text_from_mapping(data)
    if not text:
        return []
    source = SourceRef(
        path=str(path),
        format="json",
        repository=repository,
        workflow=workflow,
        run_id=run_id,
        job_name=str(data.get("job_name") or data.get("job") or ""),
        step_name=str(data.get("step_name") or data.get("step") or ""),
        url=url,
    )
    return [
        _event_from_text(
            text,
            path,
            config,
            source,
            command=str(data.get("command") or ""),
            exit_code=_int_or_none(data.get("exit_code") or data.get("conclusion_code")),
            timestamp=timestamp,
            metadata=_metadata(data, exclude={"log", "text", "raw_text", "message"}),
        )
    ]


def _events_from_workflow_run(
    data: Mapping[str, Any],
    path: Path,
    config: CorrelatorConfig,
    repository: str,
    workflow: str,
    run_id: str,
    url: str,
    timestamp: str,
) -> List[FailureEvent]:
    jobs = data.get("jobs")
    if isinstance(jobs, Mapping):
        jobs = jobs.get("jobs")
    events: List[FailureEvent] = []
    if isinstance(jobs, list):
        for job in jobs:
            if isinstance(job, Mapping) and _failed_conclusion(job):
                events.append(_event_from_job(job, path, config, repository, workflow, run_id, url, timestamp))
    text = _text_from_mapping(data)
    if text and not events:
        source = SourceRef(path=str(path), format="workflow-run-json", repository=repository, workflow=workflow, run_id=run_id, url=url)
        events.append(_event_from_text(text, path, config, source, timestamp=timestamp, metadata=_metadata(data)))
    return events


def _event_from_job(
    data: Mapping[str, Any],
    path: Path,
    config: CorrelatorConfig,
    repository: str,
    workflow: str,
    run_id: str,
    url: str,
    timestamp: str,
) -> FailureEvent:
    job_name = str(data.get("name") or data.get("job_name") or data.get("job") or "")
    failed_step = _failed_step(data)
    step_name = str(failed_step.get("name") or failed_step.get("step_name") or "") if failed_step else str(data.get("step_name") or "")
    command = str(failed_step.get("command") or data.get("command") or "") if failed_step else str(data.get("command") or "")
    text = _text_from_mapping(failed_step or {}) or _text_from_mapping(data) or json.dumps(data, sort_keys=True)
    source = SourceRef(
        path=str(path),
        format="job-json",
        repository=repository,
        workflow=workflow,
        run_id=run_id,
        job_name=job_name,
        step_name=step_name,
        url=str(data.get("html_url") or data.get("url") or url),
    )
    return _event_from_text(
        text,
        path,
        config,
        source,
        command=command,
        exit_code=_int_or_none((failed_step or {}).get("exit_code") if failed_step else data.get("exit_code")),
        timestamp=str(data.get("completed_at") or data.get("started_at") or timestamp),
        metadata=_metadata(data, exclude={"steps"}),
    )


def _event_from_text(
    text: str,
    path: Path,
    config: CorrelatorConfig,
    source: SourceRef,
    *,
    command: str = "",
    exit_code: Optional[int] = None,
    timestamp: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> FailureEvent:
    summary, normalized, tokens = summarize_and_normalize(text, config)
    labels, hits = detect_root_causes(normalized + " " + text, tokens, config)
    source = SourceRef(
        path=source.path or str(path),
        format=source.format,
        repository=canonical_repository(source.repository or _guess_repo_from_path(path), config.repository_aliases),
        workflow=source.workflow,
        run_id=source.run_id,
        job_name=source.job_name,
        step_name=source.step_name,
        url=source.url,
    )
    return FailureEvent(
        event_id=stable_event_id(
            [
                source.repository,
                source.workflow,
                source.run_id,
                source.job_name,
                source.step_name,
                normalized,
            ]
        ),
        source=source,
        raw_text=text,
        summary=summary or text[: config.max_summary_chars],
        normalized_text=normalized,
        tokens=tokens,
        root_cause_labels=labels,
        rule_hits=hits,
        severity=severity_from_labels(labels, normalized),
        language=infer_language(normalized + " " + text, labels),
        command=command,
        exit_code=exit_code,
        timestamp=timestamp,
        metadata=metadata or {},
    )


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS | JSON_EXTENSIONS | XML_EXTENSIONS


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    result = []
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _looks_like_failure_event(data: Mapping[str, Any]) -> bool:
    return "event_id" in data and "normalized_text" in data and "root_cause_labels" in data


def _is_workflow_run(data: Mapping[str, Any]) -> bool:
    return "workflow" in data and ("jobs" in data or "conclusion" in data or "run_id" in data)


def _is_job(data: Mapping[str, Any]) -> bool:
    return "steps" in data or "job_name" in data or ("conclusion" in data and ("log" in data or "text" in data))


def _failed_conclusion(data: Mapping[str, Any]) -> bool:
    conclusion = str(data.get("conclusion") or data.get("status") or "").lower()
    return conclusion in {"failure", "failed", "timed_out", "cancelled", "error"} or not conclusion


def _failed_step(data: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    steps = data.get("steps")
    if not isinstance(steps, list):
        return None
    fallback = None
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        if _text_from_mapping(step):
            fallback = step
        if _failed_conclusion(step):
            return step
    return fallback


def _repository_from_mapping(data: Mapping[str, Any], path: Path, config: CorrelatorConfig) -> str:
    repository = data.get("repository") or data.get("repo") or data.get("repository_full_name")
    if isinstance(repository, Mapping):
        repository = repository.get("full_name") or repository.get("name")
    return canonical_repository(str(repository or _guess_repo_from_path(path)), config.repository_aliases)


def _guess_repo_from_path(path: Path) -> str:
    parts = [part for part in path.parts if part not in {".", ""}]
    for index, part in enumerate(parts):
        if part in {"inputs", "examples"} and index + 1 < len(parts):
            continue
    parent = path.parent.name
    if parent and parent not in {"inputs", "examples", "work", "outputs"}:
        return parent
    return path.stem.split(".")[0] or "unknown"


def _text_from_mapping(data: Mapping[str, Any]) -> str:
    fields = [
        "summary",
        "error_summary",
        "failure_summary",
        "log_excerpt",
        "log",
        "logs",
        "raw_text",
        "text",
        "message",
        "stderr",
        "stdout",
        "annotation",
    ]
    parts: List[str] = []
    for field in fields:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if str(item).strip())
    for key in ["annotations", "errors"]:
        value = data.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    parts.append(_text_from_mapping(item))
                elif item:
                    parts.append(str(item))
    return "\n".join(part for part in parts if part).strip()


def _metadata(data: Mapping[str, Any], exclude: Optional[set[str]] = None) -> Dict[str, Any]:
    excluded = exclude or set()
    noisy = {"raw_text", "log", "logs", "text", "stderr", "stdout", "message", "summary"}
    result: Dict[str, Any] = {}
    for key, value in data.items():
        if key in noisy or key in excluded:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
    return result


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _attr(element: ET.Element, name: str) -> str:
    return str(element.attrib.get(name) or "")
