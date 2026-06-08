# Changelog

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
