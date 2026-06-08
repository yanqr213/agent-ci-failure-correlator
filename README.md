# agent-ci-failure-correlator

面向 Codex、Claude Code、GitHub Actions 高频用户的 AI/DevOps 辅助工具。它把多仓库 CI 失败导出、workflow run JSON、job log 片段、pytest/npm 错误摘要、JUnit XML 等输入归一化，聚类根因，标记“同一类失败正在多个项目重复发生”，并输出可执行的 Markdown/JSON/SARIF 报告。

The complete English guide is available below in [English](#english).

## 问题场景

维护者经常同时收到一批 CI 失败邮件：多个仓库、多个 workflow、不同 job 名称、不同路径和时间戳，但真正原因可能只有一两个，例如共享 Python 包没有安装、前端主题包漏发、GitHub runner 镜像更新、依赖锁文件过期。逐封点开邮件很慢，尤其在用 Codex 或 Claude Code 批量修复前，需要先知道哪些失败可以一起处理。

`agent-ci-failure-correlator` 的目标是把这些杂乱输入变成：

- 标准化失败事件模型
- 根因标签与置信度
- 跨仓库重复失败聚类
- 可直接发给维护者或 agent 的 Markdown 报告
- 可接入自动化与 GitHub Code Scanning 的 JSON/SARIF 报告和退出码

## 功能

- 支持输入：GitHub workflow run JSON、job JSON、JSONL/NDJSON、纯日志、JUnit XML、工具自身导出的 JSON。
- 支持 GitHub Actions 失败通知邮件：`.eml` 原文或转存 `.txt` 都会提取仓库、workflow、job、branch、run id 和 run URL。
- 日志摘要：提取错误、失败、Traceback、timeout、npm/pytest 等关键行，并保留上下文。
- 噪声归一化：隐藏路径、URL、时间戳、长哈希、版本号、持续时间和大数字。
- 规则标签：识别 Python import、JavaScript dependency、测试断言、网络、超时、权限、lint、类型检查、构建、runner 环境、容器等根因。
- 相似度聚类：结合标签相似度、token Jaccard、文本相似度、命令和语言信号。
- 置信度与证据：输出共享 token、平均/最低 pair similarity、代表性摘要。
- 报告：brief 给快速分派，Markdown 给完整人工阅读，JSON 给自动化和 agent 读，SARIF 给 GitHub Code Scanning。
- 退出码语义：可在 CI 中作为门禁使用。
- 无外部运行依赖：优先 Python 标准库，兼容 Python 3.9+。

## 安装

开发模式：

```bash
python -m pip install -e .
```

直接从源码运行：

```bash
python -m agent_ci_failure_correlator --help
```

安装后 CLI：

```bash
agent-ci-failure-correlator --help
```

## CLI 用法

把多封 GitHub Actions 失败邮件或日志放进一个目录后直接分析：

```bash
python -m agent_ci_failure_correlator analyze exported-failure-mails \
  --format markdown \
  --output reports/ci-failure-report.md
```

分析目录并输出 Markdown：

```bash
python -m agent_ci_failure_correlator analyze examples/inputs --output report.md
```

输出 JSON：

```bash
python -m agent_ci_failure_correlator analyze examples/inputs --format json --output report.json
```

输出适合直接发给维护者或 AI agent 的短摘要：

```bash
python -m agent_ci_failure_correlator analyze examples/inputs \
  --format brief \
  --output ci-failure-brief.md
```

输出 SARIF，方便上传到 GitHub Code Scanning：

```bash
python -m agent_ci_failure_correlator analyze examples/inputs \
  --format sarif \
  --output ci-failure-clusters.sarif
```

使用配置并在发现跨仓库重复失败时返回退出码 `2`：

```bash
python -m agent_ci_failure_correlator analyze examples/inputs \
  --config examples/config.json \
  --format markdown \
  --output report.md
```

生成配置模板：

```bash
python -m agent_ci_failure_correlator init-config --output ci-failure-correlator.json
```

常用参数：

- `--similarity-threshold 0.56`：调整聚类阈值，越低越容易合并。
- `--min-cluster-size 2`：只报告至少 N 个事件的聚类。
- `--format brief|markdown|json|sarif`：brief 适合先处理 CI 邮件批量分派，Markdown 适合完整阅读，JSON 适合自动化，SARIF 适合 Code Scanning。
- `--fail-on-cross-repo`：发现跨仓库重复失败时返回 `2`。
- `--fail-on-any-failure`：发现任何失败时返回 `1`。
- `--no-raw-events`：JSON 报告中不包含原始日志文本。

### 处理 GitHub Actions 失败邮件

如果你收到很多 CI 失败邮件，可以把邮件保存成 `.eml`，或把邮件正文复制成 `.txt`：

```text
Subject: [org/api-service] Run failed: CI - main
Workflow: CI
Job: pytest
Branch: main
Run URL: https://github.com/org/api-service/actions/runs/123456789

ModuleNotFoundError: No module named 'shared_auth'
```

工具会把这些字段写入事件来源：

- `source.repository`：`org/api-service`
- `source.workflow`：`CI`
- `source.job_name`：`pytest`
- `source.run_id`：`123456789`
- `source.url`：GitHub Actions 运行链接
- `metadata.branch`：`main`

邮件格式不完整也没关系；只要正文里有错误摘要，仍然会进入聚类。

## API 用法

```python
from agent_ci_failure_correlator import CorrelatorConfig, analyze_paths

config = CorrelatorConfig(similarity_threshold=0.56)
result = analyze_paths(["examples/inputs"], config=config)

for cluster in result.clusters:
    print(cluster.cluster_id, cluster.root_cause_labels, cluster.confidence)
```

分析内存中的记录：

```python
from agent_ci_failure_correlator.api import analyze_records

records = [
    {
        "repository": "org/api-service",
        "workflow": "CI",
        "job_name": "pytest",
        "log": "ModuleNotFoundError: No module named 'shared_auth'",
    }
]

result = analyze_records(records)
print(result.to_dict()["summary"])
```

## 配置

配置文件是 JSON。示例见 [`examples/config.json`](examples/config.json)：

```json
{
  "similarity_threshold": 0.56,
  "min_cluster_size": 1,
  "fail_on_cross_repo": true,
  "include_raw_events": false,
  "repository_aliases": {
    "api-service": "org/api-service"
  },
  "custom_root_causes": {
    "shared-fixture-breakage": ["golden fixture .* missing"]
  },
  "label_actions": {
    "shared-fixture-breakage": [
      "Regenerate shared fixtures and verify generated artifacts across all repositories before merging."
    ]
  }
}
```

可配置项：

- `similarity_threshold`：聚类相似度阈值。
- `min_cluster_size`：报告聚类的最小事件数。
- `max_summary_lines` / `max_summary_chars`：日志摘要上限。
- `context_lines`：错误行前后保留的上下文行数。
- `fail_on_cross_repo` / `fail_on_any_failure`：退出码门禁。
- `include_raw_events`：JSON 是否包含原始日志。
- `repository_aliases`：统一仓库别名。
- `ignore_patterns`：日志行忽略正则。
- `stop_words`：token 停用词。
- `custom_root_causes`：自定义根因标签正则。
- `label_actions`：标签对应修复建议。

## 样例输入输出

样例输入位于 `examples/inputs/`：

- `api-service-run.json` 和 `billing-run.json`：两个仓库都缺少 `shared_auth` Python 包。
- `checkout-failure.eml`：GitHub Actions 失败邮件原文，同样缺少 `shared_auth`。
- `webapp-job.json` 和 `admin-web-job.json`：两个前端仓库都找不到 `@org/ui-theme`。
- `worker.log`：pytest 断言失败。
- `search-junit.xml`：JUnit XML 断言失败。

运行：

```bash
python -m agent_ci_failure_correlator analyze examples/inputs --format markdown
```

报告会包含类似结构：

```markdown
## Cross-Repository Repeats

- **C001** `python-import`: 3 events across 3 repositories (...), confidence 0.74
- **C002** `javascript-dependency`: 2 events across 2 repositories (...), confidence 0.76
```

JSON 报告核心字段：

```json
{
  "summary": {
    "event_count": 7,
    "cluster_count": 4,
    "cross_repository_cluster_count": 2
  },
  "clusters": [
    {
      "cluster_id": "C001",
      "root_cause_labels": ["python-import"],
      "confidence": 0.74,
      "repositories": ["acme/checkout-service", "org/api-service", "org/billing-service"],
      "suggested_actions": ["Check Python dependencies..."]
    }
  ]
}
```

SARIF 报告会把每个根因聚类输出为一个 result，并在 properties 中保留事件数、仓库列表、置信度、建议动作和 run 链接，适合在 GitHub Code Scanning 中集中查看跨仓库重复失败。

brief 报告会压缩成可直接粘贴的 triage 摘要：

```markdown
# CI Failure Triage Brief

Decision: BATCH-FIX - repeated failures span repositories.
Scope: 7 failure events, 4 clusters, 2 cross-repository repeats.

Top repeated causes:
- C001 [cross-repo] python-import: 3 events across 3 repositories (...), confidence 0.74.
  Next: Check Python dependencies, package discovery, and shared environment setup.

Agent handoff:
- Fix cross-repository repeated clusters first; one shared dependency or runner change may clear multiple emails.
```

## 退出码语义

- `0`：分析完成，未触发门禁。
- `1`：开启 `--fail-on-any-failure` 后检测到失败。
- `2`：开启 `--fail-on-cross-repo` 后检测到跨仓库重复失败。
- `3`：输入、配置或 CLI 使用错误。

默认情况下，有失败也返回 `0`，便于只生成报告；需要门禁时显式开启相关参数或配置。

## GitHub Actions 集成

项目自带 CI：`.github/workflows/ci.yml` 会在 Python 3.9 到 3.13 上运行单元测试和样例分析。

在其他仓库中，你可以先收集失败日志，再运行本工具：

```yaml
name: Correlate CI Failures

on:
  workflow_dispatch:

jobs:
  correlate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install correlator
        run: python -m pip install .
      - name: Analyze exported failures
        run: |
          python -m agent_ci_failure_correlator analyze exported-failures \
            --config ci-failure-correlator.json \
            --format brief \
            --output ci-failure-brief.md
      - name: Upload report
        uses: actions/upload-artifact@v4
        with:
          name: ci-failure-brief
          path: ci-failure-brief.md
```

如果要把聚类结果上传到 GitHub Code Scanning：

```yaml
permissions:
  contents: read
  security-events: write

steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with:
      python-version: "3.12"
  - run: python -m pip install git+https://github.com/yanqr213/agent-ci-failure-correlator.git
  - run: |
      python -m agent_ci_failure_correlator analyze exported-failures \
        --format sarif \
        --output ci-failure-clusters.sarif
  - uses: github/codeql-action/upload-sarif@v3
    if: always()
    with:
      sarif_file: ci-failure-clusters.sarif
```

## 开发指南

运行测试：

```bash
python -m unittest discover -s tests -v
```

运行样例：

```bash
python -m agent_ci_failure_correlator analyze examples/inputs --format json --output report.json
```

项目结构：

- `agent_ci_failure_correlator/models.py`：结构化输入与输出模型。
- `agent_ci_failure_correlator/parsers.py`：文件发现与输入解析。
- `agent_ci_failure_correlator/normalizer.py`：日志摘要、归一化、token 化。
- `agent_ci_failure_correlator/rules.py`：根因规则与修复建议。
- `agent_ci_failure_correlator/clusterer.py`：相似度聚类与置信度。
- `agent_ci_failure_correlator/report.py`：Markdown/JSON/SARIF 报告。
- `agent_ci_failure_correlator/cli.py`：命令行入口与退出码。
- `tests/`：单元测试。
- `examples/`：配置和示例输入。

## 设计边界

本项目不调用外网，不上传日志，不依赖 LLM，也不包含任何真实 token 或个人信息。它适合作为 Codex/Claude Code 前置整理器：先把失败聚类成几类可修复问题，再把报告交给 agent 或维护者执行修复。

---

## English

`agent-ci-failure-correlator` is an AI/DevOps helper for developers who frequently work with Codex, Claude Code, and GitHub Actions. It normalizes CI failure exports, workflow run JSON, job log fragments, pytest/npm summaries, and JUnit XML files, then clusters likely shared root causes across repositories and emits Markdown, JSON, brief, and SARIF reports.

### Problem

Maintainers often receive a burst of CI failure emails from many repositories. The logs have different paths, run IDs, timestamps, job names, and stack traces, but the underlying cause may be the same: a missing shared Python package, a missing npm workspace dependency, a runner image change, stale lockfiles, or a shared fixture breakage. Before asking an AI coding agent to fix the failures, you need to know which failures belong together.

This tool produces:

- normalized failure events
- root-cause labels and confidence scores
- cross-repository repeat detection
- Markdown reports for humans and agents
- brief triage reports for fast agent handoff
- JSON reports for automation
- SARIF reports for GitHub Code Scanning
- CI-friendly exit codes

### Features

- Inputs: GitHub workflow run JSON, job JSON, JSONL/NDJSON, plain logs, JUnit XML, and exported correlator JSON.
- GitHub Actions failure notifications: parse saved `.eml` messages or copied `.txt` bodies and extract repository, workflow, job, branch, run id, and run URL.
- Log summarization: extracts error-bearing lines and local context.
- Normalization: removes volatile paths, URLs, timestamps, hashes, versions, durations, and large numbers.
- Rule labels: Python import, JavaScript dependency, assertion, network, timeout, permissions, lint, type-check, build tooling, runner environment, and container failures.
- Similarity clustering: combines label similarity, token Jaccard, text similarity, command, and language hints.
- Evidence: shared tokens, pairwise similarity, representative summaries, and suggested actions.
- Standard-library runtime, compatible with Python 3.9+.

### Installation

```bash
python -m pip install -e .
```

Run from source:

```bash
python -m agent_ci_failure_correlator --help
```

Installed CLI:

```bash
agent-ci-failure-correlator --help
```

### CLI Usage

Analyze a directory of exported GitHub Actions failure emails or logs:

```bash
python -m agent_ci_failure_correlator analyze exported-failure-mails \
  --format markdown \
  --output reports/ci-failure-report.md
```

Generate a Markdown report:

```bash
python -m agent_ci_failure_correlator analyze examples/inputs --output report.md
```

Generate JSON:

```bash
python -m agent_ci_failure_correlator analyze examples/inputs --format json --output report.json
```

Generate a compact triage brief:

```bash
python -m agent_ci_failure_correlator analyze examples/inputs \
  --format brief \
  --output ci-failure-brief.md
```

Generate SARIF for GitHub Code Scanning:

```bash
python -m agent_ci_failure_correlator analyze examples/inputs \
  --format sarif \
  --output ci-failure-clusters.sarif
```

Use a config file and fail the command when repeated failures span repositories:

```bash
python -m agent_ci_failure_correlator analyze examples/inputs \
  --config examples/config.json \
  --format markdown \
  --output report.md
```

Create a starter config:

```bash
python -m agent_ci_failure_correlator init-config --output ci-failure-correlator.json
```

Useful flags:

- `--similarity-threshold 0.56`: lower values merge more aggressively.
- `--min-cluster-size 2`: report only clusters with at least N events.
- `--format brief|markdown|json|sarif`: use brief for first-pass inbox triage, JSON for automation, and SARIF for Code Scanning.
- `--fail-on-cross-repo`: exit `2` when a repeated failure spans repositories.
- `--fail-on-any-failure`: exit `1` when any failure is found.
- `--no-raw-events`: omit raw log bodies from JSON output.

### GitHub Actions Failure Emails

If your inbox has many CI failure notifications, save them as `.eml` files or copy message bodies into `.txt` files:

```text
Subject: [org/api-service] Run failed: CI - main
Workflow: CI
Job: pytest
Branch: main
Run URL: https://github.com/org/api-service/actions/runs/123456789

ModuleNotFoundError: No module named 'shared_auth'
```

The parser writes these fields into event source metadata:

- `source.repository`: `org/api-service`
- `source.workflow`: `CI`
- `source.job_name`: `pytest`
- `source.run_id`: `123456789`
- `source.url`: GitHub Actions run URL
- `metadata.branch`: `main`

Incomplete email formats are still useful; if the body contains an error summary, the event can still be clustered.

### API Usage

```python
from agent_ci_failure_correlator import CorrelatorConfig, analyze_paths

config = CorrelatorConfig(similarity_threshold=0.56)
result = analyze_paths(["examples/inputs"], config=config)

for cluster in result.clusters:
    print(cluster.cluster_id, cluster.root_cause_labels, cluster.confidence)
```

Analyze records already loaded in memory:

```python
from agent_ci_failure_correlator.api import analyze_records

records = [
    {
        "repository": "org/api-service",
        "workflow": "CI",
        "job_name": "pytest",
        "log": "ModuleNotFoundError: No module named 'shared_auth'",
    }
]

result = analyze_records(records)
print(result.to_dict()["summary"])
```

### Configuration

Config files are JSON. See `examples/config.json`.

Important options:

- `similarity_threshold`: clustering threshold.
- `min_cluster_size`: minimum reported cluster size.
- `max_summary_lines` / `max_summary_chars`: log summary limits.
- `context_lines`: lines around error matches to keep.
- `fail_on_cross_repo` / `fail_on_any_failure`: CI gate behavior.
- `include_raw_events`: include or omit raw logs in JSON.
- `repository_aliases`: canonicalize repository names.
- `ignore_patterns`: regexes for ignored log lines.
- `stop_words`: token stop words.
- `custom_root_causes`: custom label regexes.
- `label_actions`: custom remediation text per label.

### Example Inputs and Outputs

Sample inputs live in `examples/inputs/` and include Python import failures, a GitHub Actions failure email, npm module failures, pytest assertion failures, and JUnit XML.

Run:

```bash
python -m agent_ci_failure_correlator analyze examples/inputs --format markdown
```

You should see a `Cross-Repository Repeats` section with clusters such as `python-import` and `javascript-dependency`.

The bundled inputs currently produce 7 events, 4 clusters, and 2 cross-repository repeat clusters. The `python-import` cluster includes 3 repositories, including the saved GitHub Actions email from `acme/checkout-service`.

```markdown
## Cross-Repository Repeats

- **C001** `python-import`: 3 events across 3 repositories (...), confidence 0.74
- **C002** `javascript-dependency`: 2 events across 2 repositories (...), confidence 0.76
```

### Exit Codes

- `0`: analysis completed and no configured gate fired.
- `1`: `--fail-on-any-failure` was enabled and failures were found.
- `2`: `--fail-on-cross-repo` was enabled and repeated failures span repositories.
- `3`: input, config, or CLI usage error.

By default, failures do not make the command fail; the tool is report-first unless you enable gate flags.

### GitHub Actions

The repository includes `.github/workflows/ci.yml`, which runs unit tests and sample analysis on Python 3.9 through 3.13.

Example usage in another workflow:

```yaml
name: Correlate CI Failures

on:
  workflow_dispatch:

jobs:
  correlate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install correlator
        run: python -m pip install .
      - name: Analyze exported failures
        run: |
          python -m agent_ci_failure_correlator analyze exported-failures \
            --config ci-failure-correlator.json \
            --format brief \
            --output ci-failure-brief.md
      - name: Upload report
        uses: actions/upload-artifact@v4
        with:
          name: ci-failure-brief
          path: ci-failure-brief.md
```

Upload SARIF in GitHub Actions:

```yaml
- run: python -m agent_ci_failure_correlator analyze exported-failures --format sarif --output ci-failure-clusters.sarif
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with:
    sarif_file: ci-failure-clusters.sarif
```

### Development

```bash
python -m unittest discover -s tests -v
python -m agent_ci_failure_correlator analyze examples/inputs --format json --output report.json
```

The project has no runtime dependency outside the Python standard library. It does not call external services, upload logs, or contain real credentials.
