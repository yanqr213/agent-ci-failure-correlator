"""Fetch recent GitHub Actions failures into correlator-friendly JSONL records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DEFAULT_CONCLUSIONS = ("failure", "timed_out", "cancelled")
SECRET_PATTERNS = [
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]+"),
    re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s]+"),
    re.compile(r"(?i)((?:token|password|secret|api[_-]?key)\s*[:=]\s*)[^\s]+"),
]


@dataclass(frozen=True)
class GitHubFetchOptions:
    """Options for collecting GitHub Actions failures."""

    per_repo_limit: int = 20
    max_pages: int = 3
    branch: str = ""
    workflow: str = ""
    since: str = ""
    days: int = 14
    include_logs: bool = True
    log_chars: int = 20000
    conclusions: Tuple[str, ...] = DEFAULT_CONCLUSIONS


@dataclass
class GitHubFetchResult:
    """Fetched records plus non-fatal collection warnings."""

    records: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    repositories: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GitHubRepositoryDiscoveryOptions:
    """Options for discovering repositories before collecting failures."""

    per_owner_limit: int = 100
    max_pages: int = 3
    repo_type: str = "all"
    include_archived: bool = False
    include_forks: bool = False
    name_pattern: str = ""


@dataclass
class GitHubRepositoryDiscoveryResult:
    """Repositories discovered from one or more GitHub owners."""

    repositories: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    owners: List[str] = field(default_factory=list)


class GitHubApiError(RuntimeError):
    """Raised when the GitHub API cannot satisfy a request."""

    def __init__(self, message: str, *, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """Small standard-library GitHub API client used by the CLI and tests."""

    def __init__(self, *, token: str = "", base_url: str = "https://api.github.com", timeout: int = 30):
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_workflow_runs(
        self,
        repository: str,
        *,
        page: int,
        per_page: int,
        branch: str = "",
        created: str = "",
    ) -> List[Mapping[str, Any]]:
        params: Dict[str, Any] = {"status": "completed", "per_page": per_page, "page": page}
        if branch:
            params["branch"] = branch
        if created:
            params["created"] = created
        data = self._request_json(f"/repos/{repository}/actions/runs", params=params)
        runs = data.get("workflow_runs", []) if isinstance(data, Mapping) else []
        return [run for run in runs if isinstance(run, Mapping)]

    def get_repository(self, repository: str) -> Mapping[str, Any]:
        """Return repository metadata, including the default branch."""

        data = self._request_json(f"/repos/{repository}")
        return data if isinstance(data, Mapping) else {}

    def list_current_workflow_runs(
        self,
        repository: str,
        *,
        page: int,
        per_page: int,
        branch: str = "",
        head_sha: str = "",
        exclude_pull_requests: bool = False,
    ) -> List[Mapping[str, Any]]:
        """List recent workflow runs without forcing completed status."""

        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if branch:
            params["branch"] = branch
        if head_sha:
            params["head_sha"] = head_sha
        if exclude_pull_requests:
            params["exclude_pull_requests"] = "true"
        data = self._request_json(f"/repos/{repository}/actions/runs", params=params)
        runs = data.get("workflow_runs", []) if isinstance(data, Mapping) else []
        return [run for run in runs if isinstance(run, Mapping)]

    def list_pull_requests(
        self,
        repository: str,
        *,
        page: int,
        per_page: int,
        state: str = "open",
    ) -> List[Mapping[str, Any]]:
        """List pull requests for current-status audits."""

        data = self._request_json(
            f"/repos/{repository}/pulls",
            params={"state": state, "per_page": per_page, "page": page},
        )
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, Mapping)]

    def list_run_jobs(self, repository: str, run_id: str) -> List[Mapping[str, Any]]:
        jobs: List[Mapping[str, Any]] = []
        for page in range(1, 11):
            data = self._request_json(
                f"/repos/{repository}/actions/runs/{run_id}/jobs",
                params={"per_page": 100, "page": page},
            )
            batch = data.get("jobs", []) if isinstance(data, Mapping) else []
            jobs.extend(job for job in batch if isinstance(job, Mapping))
            if not isinstance(batch, list) or len(batch) < 100:
                break
        return jobs

    def list_owner_repositories(
        self,
        owner: str,
        *,
        page: int,
        per_page: int,
        repo_type: str = "all",
    ) -> List[Mapping[str, Any]]:
        """List repositories for a GitHub organization or public user profile."""

        owner = _validate_owner(owner)
        params = {"per_page": per_page, "page": page, "type": _repository_type(repo_type, endpoint="org")}
        try:
            data = self._request_json(f"/orgs/{owner}/repos", params=params)
        except GitHubApiError as exc:
            if exc.status_code != 404:
                raise
            user_params = {
                "per_page": per_page,
                "page": page,
                "type": _repository_type(repo_type, endpoint="user"),
            }
            data = self._request_json(f"/users/{owner}/repos", params=user_params)
        if not isinstance(data, list):
            return []
        return [repo for repo in data if isinstance(repo, Mapping)]

    def download_job_log(self, repository: str, job_id: str) -> str:
        body = self._request_bytes(f"/repos/{repository}/actions/jobs/{job_id}/logs")
        return _decode_log_body(body)

    def _request_json(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> Any:
        body = self._request_bytes(path, params=params, accept="application/vnd.github+json")
        return json.loads(body.decode("utf-8"))

    def _request_bytes(
        self,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        accept: str = "application/vnd.github+json",
    ) -> bytes:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({key: value for key, value in params.items() if value not in {"", None}})
        request = urllib.request.Request(url)
        request.add_header("Accept", accept)
        request.add_header("User-Agent", "agent-ci-failure-correlator")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise GitHubApiError(
                f"GitHub API returned HTTP {exc.code} for {path}: {detail}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubApiError(f"GitHub API request failed for {path}: {exc.reason}") from exc


def discover_repositories(
    owners: Sequence[str],
    options: Optional[GitHubRepositoryDiscoveryOptions] = None,
    *,
    client: Optional[GitHubClient] = None,
) -> GitHubRepositoryDiscoveryResult:
    """Discover accessible repositories for GitHub user or organization owners."""

    opts = options or GitHubRepositoryDiscoveryOptions()
    api = client or GitHubClient()
    matcher = re.compile(opts.name_pattern) if opts.name_pattern else None
    result = GitHubRepositoryDiscoveryResult(owners=[_validate_owner(owner) for owner in owners])
    seen = set()
    for owner in result.owners:
        collected_for_owner = 0
        per_page = 100
        for page in range(1, max(1, opts.max_pages) + 1):
            try:
                repos = api.list_owner_repositories(
                    owner,
                    page=page,
                    per_page=per_page,
                    repo_type=opts.repo_type,
                )
            except GitHubApiError as exc:
                result.warnings.append(f"{owner}: {exc}")
                break
            if not repos:
                break
            for repo in repos:
                full_name = str(repo.get("full_name") or "")
                try:
                    normalized = _validate_repository(full_name)
                except ValueError:
                    continue
                if repo.get("archived") and not opts.include_archived:
                    continue
                if repo.get("fork") and not opts.include_forks:
                    continue
                if matcher and not matcher.search(normalized):
                    continue
                key = normalized.lower()
                if key in seen:
                    continue
                seen.add(key)
                result.repositories.append(normalized)
                collected_for_owner += 1
                if collected_for_owner >= max(1, opts.per_owner_limit):
                    break
            if collected_for_owner >= max(1, opts.per_owner_limit) or len(repos) < per_page:
                break
    return result


def fetch_failed_jobs(
    repositories: Sequence[str],
    options: Optional[GitHubFetchOptions] = None,
    *,
    client: Optional[GitHubClient] = None,
) -> GitHubFetchResult:
    """Collect failed GitHub Actions jobs as JSON-compatible records."""

    opts = options or GitHubFetchOptions()
    api = client or GitHubClient()
    result = GitHubFetchResult(repositories=list(repositories))
    created = _created_query(opts)
    conclusions = {item.lower() for item in opts.conclusions}
    for repository in repositories:
        repo = _validate_repository(repository)
        collected = 0
        for page in range(1, max(1, opts.max_pages) + 1):
            try:
                runs = api.list_workflow_runs(
                    repo,
                    page=page,
                    per_page=min(100, max(1, opts.per_repo_limit * 3)),
                    branch=opts.branch,
                    created=created,
                )
            except GitHubApiError as exc:
                result.warnings.append(f"{repo}: {exc}")
                break
            if not runs:
                break
            for run in runs:
                if collected >= opts.per_repo_limit:
                    break
                if not _run_matches(run, opts.workflow, conclusions):
                    continue
                run_id = str(run.get("id") or run.get("run_id") or "")
                if not run_id:
                    continue
                try:
                    jobs = api.list_run_jobs(repo, run_id)
                except GitHubApiError as exc:
                    result.warnings.append(f"{repo} run {run_id}: {exc}")
                    result.records.append(_run_fallback_record(repo, run))
                    collected += 1
                    continue
                failed_jobs = [job for job in jobs if _job_failed(job, conclusions)]
                if not failed_jobs:
                    result.records.append(_run_fallback_record(repo, run))
                    collected += 1
                    continue
                for job in failed_jobs:
                    if collected >= opts.per_repo_limit:
                        break
                    result.records.append(_job_record(repo, run, job, opts, api, result.warnings))
                    collected += 1
            if collected >= opts.per_repo_limit or len(runs) < min(100, max(1, opts.per_repo_limit * 3)):
                break
    return result


def fetch_failed_jobs_for_owners(
    owners: Sequence[str],
    fetch_options: Optional[GitHubFetchOptions] = None,
    discovery_options: Optional[GitHubRepositoryDiscoveryOptions] = None,
    *,
    client: Optional[GitHubClient] = None,
) -> GitHubFetchResult:
    """Discover owner repositories and collect recent failed GitHub Actions jobs."""

    api = client or GitHubClient()
    discovered = discover_repositories(owners, discovery_options, client=api)
    result = fetch_failed_jobs(discovered.repositories, fetch_options, client=api)
    result.warnings = discovered.warnings + result.warnings
    result.repositories = list(discovered.repositories)
    return result


def render_jsonl(records: Iterable[Mapping[str, Any]]) -> str:
    """Render fetched records as newline-delimited JSON."""

    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)


def read_repositories(values: Sequence[str], repo_file: str = "") -> List[str]:
    """Merge repositories from CLI arguments and an optional newline-delimited file."""

    repos = [item.strip() for item in values if item and item.strip()]
    if repo_file:
        with open(repo_file, "r", encoding="utf-8") as handle:
            for line in handle:
                value = line.strip()
                if value and not value.startswith("#"):
                    repos.append(value)
    seen = set()
    ordered: List[str] = []
    for repo in repos:
        normalized = _validate_repository(repo)
        key = normalized.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(normalized)
    return ordered


def token_from_environment(names: str) -> str:
    """Read the first available token from a comma-separated env-var list."""

    for name in [item.strip() for item in names.split(",") if item.strip()]:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def redact_log(text: str, *, limit: int = 20000) -> str:
    """Trim and redact common secret shapes before writing fetched logs to disk."""

    redacted = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: _redaction(match), redacted)
    if limit > 0 and len(redacted) > limit:
        return redacted[:limit].rstrip() + "\n... [truncated]"
    return redacted


def _job_record(
    repository: str,
    run: Mapping[str, Any],
    job: Mapping[str, Any],
    options: GitHubFetchOptions,
    client: GitHubClient,
    warnings: List[str],
) -> Dict[str, Any]:
    job_id = str(job.get("id") or "")
    log = ""
    if options.include_logs and job_id:
        try:
            log = client.download_job_log(repository, job_id)
        except GitHubApiError as exc:
            warnings.append(f"{repository} job {job_id}: {exc}")
    if not log:
        log = _fallback_job_text(run, job)
    return {
        "repository": repository,
        "workflow": str(run.get("name") or run.get("workflow_name") or ""),
        "run_id": str(run.get("id") or ""),
        "run_number": run.get("run_number"),
        "run_attempt": run.get("run_attempt"),
        "run_conclusion": str(run.get("conclusion") or ""),
        "job_id": job_id,
        "job_name": str(job.get("name") or ""),
        "status": str(job.get("status") or ""),
        "conclusion": str(job.get("conclusion") or ""),
        "branch": str(run.get("head_branch") or ""),
        "commit": str(run.get("head_sha") or ""),
        "event": str(run.get("event") or ""),
        "created_at": str(run.get("created_at") or ""),
        "updated_at": str(job.get("completed_at") or run.get("updated_at") or ""),
        "started_at": str(job.get("started_at") or ""),
        "completed_at": str(job.get("completed_at") or ""),
        "url": str(job.get("html_url") or run.get("html_url") or ""),
        "run_url": str(run.get("html_url") or ""),
        "steps": _step_records(job.get("steps")),
        "log": redact_log(log, limit=options.log_chars),
        "metadata_source": "github-actions-api",
    }


def _run_fallback_record(repository: str, run: Mapping[str, Any]) -> Dict[str, Any]:
    text = "\n".join(
        part
        for part in [
            f"Workflow run concluded {run.get('conclusion') or 'unknown'}.",
            str(run.get("display_title") or ""),
            str(run.get("html_url") or ""),
        ]
        if part
    )
    return {
        "repository": repository,
        "workflow": str(run.get("name") or run.get("workflow_name") or ""),
        "run_id": str(run.get("id") or ""),
        "run_number": run.get("run_number"),
        "run_attempt": run.get("run_attempt"),
        "run_conclusion": str(run.get("conclusion") or ""),
        "branch": str(run.get("head_branch") or ""),
        "commit": str(run.get("head_sha") or ""),
        "event": str(run.get("event") or ""),
        "created_at": str(run.get("created_at") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "url": str(run.get("html_url") or ""),
        "log": text,
        "metadata_source": "github-actions-api",
    }


def _run_matches(run: Mapping[str, Any], workflow: str, conclusions: set[str]) -> bool:
    conclusion = str(run.get("conclusion") or "").lower()
    if conclusion not in conclusions:
        return False
    if not workflow:
        return True
    wanted = workflow.lower()
    candidates = [
        str(run.get("name") or ""),
        str(run.get("workflow_name") or ""),
        str(run.get("path") or ""),
    ]
    return any(wanted == item.lower() or wanted in item.lower() for item in candidates if item)


def _job_failed(job: Mapping[str, Any], conclusions: set[str]) -> bool:
    conclusion = str(job.get("conclusion") or job.get("status") or "").lower()
    return conclusion in conclusions


def _step_records(steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    records: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        records.append(
            {
                "name": str(step.get("name") or ""),
                "status": str(step.get("status") or ""),
                "conclusion": str(step.get("conclusion") or ""),
                "number": step.get("number"),
                "started_at": str(step.get("started_at") or ""),
                "completed_at": str(step.get("completed_at") or ""),
            }
        )
    return records


def _fallback_job_text(run: Mapping[str, Any], job: Mapping[str, Any]) -> str:
    steps = job.get("steps")
    step_items = steps if isinstance(steps, list) else []
    failed_steps = [
        str(step.get("name") or "")
        for step in step_items
        if isinstance(step, Mapping) and str(step.get("conclusion") or "").lower() in DEFAULT_CONCLUSIONS
    ]
    parts = [
        f"Workflow {run.get('name') or 'unknown'} run {run.get('id') or ''} concluded {run.get('conclusion') or 'unknown'}.",
        f"Job {job.get('name') or 'unknown'} concluded {job.get('conclusion') or 'unknown'}.",
    ]
    if failed_steps:
        parts.append("Failed steps: " + ", ".join(failed_steps))
    return "\n".join(parts)


def _created_query(options: GitHubFetchOptions) -> str:
    if options.since:
        return ">=" + options.since
    if options.days and options.days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=options.days)
        return ">=" + cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    return ""


def _validate_repository(repository: str) -> str:
    cleaned = repository.strip().strip("/")
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", cleaned):
        raise ValueError(f"Repository must use owner/name format: {repository}")
    return cleaned


def _validate_owner(owner: str) -> str:
    cleaned = owner.strip().strip("/")
    if not re.match(r"^[A-Za-z0-9_.-]+$", cleaned):
        raise ValueError(f"GitHub owner must be a user or organization name: {owner}")
    return cleaned


def _repository_type(repo_type: str, *, endpoint: str) -> str:
    normalized = (repo_type or "all").strip().lower()
    org_types = {"all", "public", "private", "forks", "sources", "member"}
    user_types = {"all", "owner", "member"}
    if endpoint == "org":
        return normalized if normalized in org_types else "all"
    return normalized if normalized in user_types else "all"


def _decode_log_body(body: bytes) -> str:
    if body.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            parts = []
            for name in sorted(archive.namelist()):
                if name.endswith("/"):
                    continue
                parts.append(f"===== {name} =====")
                parts.append(archive.read(name).decode("utf-8", errors="replace"))
            return "\n".join(parts)
    return body.decode("utf-8", errors="replace")


def _redaction(match: re.Match[str]) -> str:
    if match.lastindex:
        return match.group(1) + "[REDACTED]"
    return "[REDACTED]"
