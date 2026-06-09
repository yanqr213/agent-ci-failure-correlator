# Changelog

## 0.10.0 - 2026-06-09

- Added `audit-inbox --format action-plan` to turn CI failure inbox audits into issue/PR-ready repair plans.
- Action plans now separate current repair work, unknown events, archive candidates, audit warnings, and copy-ready agent prompts.
- Exported `render_inbox_action_plan_markdown` in the Python API.
- Expanded tests, CI smoke coverage, and Chinese/English README usage notes for inbox action plans.

## 0.9.0 - 2026-06-09

- Added `audit-inbox` to parse exported CI failure emails/logs, extract referenced repositories, and classify historical failures as current, stale, or unknown against live GitHub Actions heads.
- Added stable JSON schema `agent-ci-failure-correlator.inbox-audit.v1` and Markdown reports with `ACTION NEEDED`, `REVIEW NEEDED`, and `CLEAR` decisions.
- Added Python API helpers `audit_inbox_paths`, `render_inbox_audit_json`, `render_inbox_audit_markdown`, `InboxAuditResult`, and `InboxEventAudit`.
- Improved `.eml` parsing for GitHub notification fields stored as mail headers, including Workflow, Branch, Job, and Run URL.
- Added workflow/branch-aware matching so unrelated current failures in the same repository are reported as review-needed instead of false current matches.

## 0.8.0 - 2026-06-09

- Added `audit-github` to report current GitHub Actions health for default branches and open pull requests.
- Added stable JSON schema `agent-ci-failure-correlator.current-actions.v1` plus Markdown reports with `CLEAR` / `ACTION NEEDED` decisions.
- Added Python API helpers `audit_current_actions`, `audit_current_actions_for_owners`, `render_current_audit_json`, and `render_current_audit_markdown`.
- Added CLI gates for `--fail-on-current-problem`, `--ignore-pending`, and default-branch-only audits.
- Expanded tests, CI smoke coverage, and Chinese/English documentation for current-status audits.

## 0.7.0 - 2026-06-09

- Added GitHub owner and organization repository discovery for `fetch-github` via repeatable `--owner`.
- Added discovery filters for repository name regex, archived repositories, forks, repository type, per-owner limits, and pagination.
- Added Python API helpers `discover_repositories` and `fetch_failed_jobs_for_owners` with discovery option/result dataclasses.
- Added unit and CLI coverage for owner discovery, filtering, warning handling, and mixed owner plus explicit repository fetches.
- Expanded Chinese and English documentation with owner-level fetch workflows for maintainers handling many CI failure emails.

## 0.6.0 - 2026-06-09

- Added `queue` and `queue-json` report formats that turn failure clusters into prioritized repair tasks.
- Added task scoring, owner hints, affected job tables, run links, suggested actions, and ready-to-use agent prompts.
- Exposed `build_triage_queue`, `render_queue_markdown`, `render_queue_json`, and `TriageTask` in the Python API.
- Added CLI `--max-tasks` for queue reports and GitHub Actions smoke coverage for the new formats.
- Expanded Chinese and English documentation for repair queue workflows.

## 0.5.0 - 2026-06-08

- Added `fetch-github` to collect recent failed GitHub Actions jobs into JSONL records.
- Added standard-library GitHub API client, job log download, ZIP/text log decoding, and common secret redaction.
- Exported `GitHubClient`, `GitHubFetchOptions`, `GitHubFetchResult`, and `fetch_failed_jobs` for Python API users.
- Added Chinese and English documentation for GitHub fetching, token handling, and the fetch-then-analyze workflow.
- Added unit coverage for fetcher behavior, CLI integration, JSONL rendering, and redaction.

## 0.4.0 - 2026-06-08

- Added brief reports and SARIF output for CI failure clusters.
- Improved GitHub Actions failure email parsing and report metadata.
- Expanded tests and CI sample analysis across supported Python versions.
