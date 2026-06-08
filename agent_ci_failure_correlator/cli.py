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
from .models import AnalysisResult
from .report import render_json, render_markdown

EXIT_SUCCESS = 0
EXIT_FAILURES_FOUND = 1
EXIT_CROSS_REPOSITORY_REPEATS = 2
EXIT_USAGE_ERROR = 3


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "version":
        print(__version__)
        return EXIT_SUCCESS
    if args.command == "init-config":
        return _init_config(args)
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
    analyze.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Report format.")
    analyze.add_argument("--similarity-threshold", type=float, help="Override similarity threshold.")
    analyze.add_argument("--min-cluster-size", type=int, help="Override minimum cluster size.")
    analyze.add_argument("--fail-on-cross-repo", action="store_true", help="Exit 2 when repeated failures span repositories.")
    analyze.add_argument("--fail-on-any-failure", action="store_true", help="Exit 1 when any failure is detected.")
    analyze.add_argument("--no-raw-events", action="store_true", help="Omit raw event bodies from JSON output.")

    init_config = subparsers.add_parser("init-config", help="Write a starter JSON config file.")
    init_config.add_argument("-o", "--output", default="ci-failure-correlator.json", help="Config path to create.")
    init_config.add_argument("--force", action="store_true", help="Overwrite an existing config file.")

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
    output = render_json(result) if args.format == "json" else render_markdown(result)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
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
