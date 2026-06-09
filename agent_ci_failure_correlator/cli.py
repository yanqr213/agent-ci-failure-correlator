"""Command-line interface for agent-ci-failure-correlator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional

from . import __version__
from .api import analyze_paths
from .config import CorrelatorConfig, load_config
from .github_fetcher import (
    GitHubClient,
    GitHubFetchOptions,
    GitHubRepositoryDiscoveryOptions,
    fetch_failed_jobs,
    fetch_failed_jobs_for_owners,
    read_repositories,
    render_jsonl,
    token_from_environment,
)
from .models import AnalysisResult
from .report import render_brief, render_json, render_markdown, render_sarif
from .triage import render_queue_json, render_queue_markdown

EXIT_SUCCESS = 0
EXIT_FAILURES_FOUND = 1
EXIT_CROSS_REPOSITORY_REPEATS = 2
EXIT_USAGE_ERROR = 3
_github_client_factory = GitHubClient


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "version":
        print(__version__)
        return EXIT_SUCCESS
    if args.command == "init-config":
        return _init_config(args)
    if args.command == "fetch-github":
        return _fetch_github(args)
    if args.command == "analyze":
        return _analyze(args)
    parser.print_help(sys.stderr)
    return EXIT_USAGE_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-ci-failure-correlator",
        description="Normalize and correlate recurring CI failures across repositories.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="Analyze CI failure inputs and write a report.")
    analyze.add_argument("inputs", nargs="+", help="Input files or directories (.json, .jsonl, .xml, .log, .txt).")
    analyze.add_argument("-c", "--config", help="Path to JSON configuration file.")
    analyze.add_argument("-o", "--output", help="Output report path. Defaults to stdout.")
    analyze.add_argument("--format", choices=["brief", "markdown", "json", "sarif", "queue", "queue-json"], default="markdown", help="Report format.")
    analyze.add_argument("--similarity-threshold", type=float, help="Override similarity threshold.")
    analyze.add_argument("--min-cluster-size", type=int, help="Override minimum cluster size.")
    analyze.add_argument("--max-tasks", type=int, help="Limit queue/queue-json output to the top N repair tasks.")
    analyze.add_argument("--fail-on-cross-repo", action="store_true", help="Exit 2 when repeated failures span repositories.")
    analyze.add_argument("--fail-on-any-failure", action="store_true", help="Exit 1 when any failure is detected.")
    analyze.add_argument("--no-raw-events", action="store_true", help="Omit raw event bodies from JSON output.")

    init_config = subparsers.add_parser("init-config", help="Write a starter JSON config file.")
    init_config.add_argument("-o", "--output", default="ci-failure-correlator.json", help="Config path to create.")
    init_config.add_argument("--force", action="store_true", help="Overwrite an existing config file.")

    fetch = subparsers.add_parser("fetch-github", help="Fetch recent GitHub Actions failures as JSONL records.")
    fetch.add_argument("repositories", nargs="*", help="Repositories in owner/name format.")
    fetch.add_argument("--repo-file", help="Newline-delimited repository list. Lines starting with # are ignored.")
    fetch.add_argument("--owner", action="append", default=[], help="Discover repositories from a GitHub user or organization. Repeatable.")
    fetch.add_argument("--repo-name-pattern", help="Regex filter applied to discovered owner/name repositories.")
    fetch.add_argument("--include-archived", action="store_true", help="Include archived repositories discovered from --owner.")
    fetch.add_argument("--include-forks", action="store_true", help="Include forked repositories discovered from --owner.")
    fetch.add_argument("--repo-type", choices=["all", "public", "private", "forks", "sources", "member", "owner"], default="all", help="GitHub repository type filter for owner discovery.")
    fetch.add_argument("--repo-limit", type=int, default=100, help="Maximum repositories to discover per owner.")
    fetch.add_argument("--repo-pages", type=int, default=3, help="Maximum owner repository pages to inspect.")
    fetch.add_argument("-o", "--output", help="Output JSONL path. Defaults to stdout.")
    fetch.add_argument("--token-env", default="GITHUB_TOKEN,GH_TOKEN", help="Comma-separated environment variables to read a GitHub token from.")
    fetch.add_argument("--branch", help="Only fetch runs from this branch.")
    fetch.add_argument("--workflow", help="Only fetch workflow runs whose name or path contains this value.")
    fetch.add_argument("--since", help="Fetch runs created on or after this ISO timestamp, for example 2026-06-01T00:00:00Z.")
    fetch.add_argument("--days", type=int, default=14, help="Fetch runs from the last N days when --since is not set.")
    fetch.add_argument("--limit", type=int, default=20, help="Maximum failed job records per repository.")
    fetch.add_argument("--max-pages", type=int, default=3, help="Maximum workflow-run pages to inspect per repository.")
    fetch.add_argument("--conclusion", action="append", choices=["failure", "timed_out", "cancelled"], help="Failed run conclusion to include. Repeatable.")
    fetch.add_argument("--no-logs", action="store_true", help="Do not download job logs; emit run/job metadata only.")
    fetch.add_argument("--log-chars", type=int, default=20000, help="Maximum redacted log characters per job.")
    fetch.add_argument("--api-url", default="https://api.github.com", help="GitHub API base URL.")
    fetch.add_argument("--fail-on-warning", action="store_true", help="Return usage error when any repository could not be fetched.")

    subparsers.add_parser("version", help="Print package version.")
    return parser


def _analyze(args: argparse.Namespace) -> int:
    overrides = {}
    if args.similarity_threshold is not None:
        overrides["similarity_threshold"] = args.similarity_threshold
    if args.min_cluster_size is not None:
        overrides["min_cluster_size"] = args.min_cluster_size
    if args.fail_on_cross_repo:
        overrides["fail_on_cross_repo"] = True
    if args.fail_on_any_failure:
        overrides["fail_on_any_failure"] = True
    if args.no_raw_events:
        overrides["include_raw_events"] = False
    try:
        result = analyze_paths(args.inputs, config_path=args.config, overrides=overrides)
    except Exception as exc:
        print(f"agent-ci-failure-correlator: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    if not result.config.get("include_raw_events", True):
        _drop_raw_events(result)
    if args.format == "json":
        output = render_json(result)
    elif args.format == "sarif":
        output = render_sarif(result)
    elif args.format == "brief":
        output = render_brief(result)
    elif args.format == "queue":
        output = render_queue_markdown(result, max_tasks=args.max_tasks)
    elif args.format == "queue-json":
        output = render_queue_json(result, max_tasks=args.max_tasks)
    else:
        output = render_markdown(result)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return _exit_code(result)


def _init_config(args: argparse.Namespace) -> int:
    path = Path(args.output)
    if path.exists() and not args.force:
        print(f"Config already exists: {path}. Use --force to overwrite.", file=sys.stderr)
        return EXIT_USAGE_ERROR
    config = CorrelatorConfig.default().to_dict()
    sample = {
        **config,
        "repository_aliases": {
            "org/api": "platform-api",
            "api": "platform-api",
        },
        "custom_root_causes": {
            "shared-fixture-breakage": ["fixture .* not found", "snapshot .* obsolete"],
        },
        "label_actions": {
            "shared-fixture-breakage": [
                "Regenerate shared fixtures and check whether generated test assets changed across repositories."
            ]
        },
    }
    path.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return EXIT_SUCCESS


def _fetch_github(args: argparse.Namespace) -> int:
    try:
        repositories = read_repositories(args.repositories, args.repo_file or "")
        owners = [owner.strip() for owner in args.owner if owner and owner.strip()]
        if not repositories and not owners:
            raise ValueError("At least one repository, --repo-file entry, or --owner is required.")
        options = GitHubFetchOptions(
            per_repo_limit=max(1, args.limit),
            max_pages=max(1, args.max_pages),
            branch=args.branch or "",
            workflow=args.workflow or "",
            since=args.since or "",
            days=max(0, args.days),
            include_logs=not args.no_logs,
            log_chars=max(0, args.log_chars),
            conclusions=tuple(args.conclusion or ["failure", "timed_out", "cancelled"]),
        )
        client = _github_client_factory(token=token_from_environment(args.token_env), base_url=args.api_url)
        if owners:
            discovery = GitHubRepositoryDiscoveryOptions(
                per_owner_limit=max(1, args.repo_limit),
                max_pages=max(1, args.repo_pages),
                repo_type=args.repo_type,
                include_archived=args.include_archived,
                include_forks=args.include_forks,
                name_pattern=args.repo_name_pattern or "",
            )
            result = fetch_failed_jobs_for_owners(owners, options, discovery, client=client)
            if repositories:
                discovered = {repo.lower() for repo in result.repositories}
                remaining_repositories = [repo for repo in repositories if repo.lower() not in discovered]
                explicit_result = fetch_failed_jobs(remaining_repositories, options, client=client)
                result.records.extend(explicit_result.records)
                result.warnings.extend(explicit_result.warnings)
                seen = {repo.lower() for repo in result.repositories}
                for repo in explicit_result.repositories:
                    if repo.lower() not in seen:
                        result.repositories.append(repo)
                        seen.add(repo.lower())
        else:
            result = fetch_failed_jobs(repositories, options, client=client)
    except Exception as exc:
        print(f"agent-ci-failure-correlator: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    output = render_jsonl(result.records)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.fail_on_warning and result.warnings:
        return EXIT_USAGE_ERROR
    return EXIT_SUCCESS


def _exit_code(result: AnalysisResult) -> int:
    fail_on_cross = bool(result.config.get("fail_on_cross_repo"))
    fail_on_any = bool(result.config.get("fail_on_any_failure"))
    if fail_on_cross and result.has_cross_repository_repeats:
        return EXIT_CROSS_REPOSITORY_REPEATS
    if fail_on_any and result.has_failures:
        return EXIT_FAILURES_FOUND
    return EXIT_SUCCESS


def _drop_raw_events(result: AnalysisResult) -> None:
    for event in result.events:
        event.raw_text = ""
    for cluster in result.clusters:
        for event in cluster.events:
            event.raw_text = ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
