# Agent Hour Tracker

Agent Hour Tracker is a local CLI that measures cumulative Codex agent-hours from completed session turns.

If you inspect your own local Codex activity, start with the sanitized Archive Score card:

```bash
uvx codex-agent-hour-tracker --share
```

The command reads local session JSONL files and reports completed root and delegated turns. It does not decode conversation content for timing. Use explicit date bounds when you need a reproducible custom report.

## Synthetic Archive Score example

The following card uses fabricated dates and values. It is an example, not a report from a person or machine:

```text
CODEX AGENT-HOUR SCORE

30 complete calendar days | 2025-01-01 to 2025-01-30
-----------------------------------------------------
Agent-hours/day: 2.00
Total agent-hours: 60.00
Peak day: 4.00
Completed turns: 12
Active days: 10/30

Archive Score | methodology v1 | tracker v0.1.1
Calculated locally. No conversation content uploaded.
```

Agent-hours are cumulative completed-turn durations. If turns overlap, the tracker sums each turn independently. The total measures cumulative runtime, not elapsed wall-clock time.

Normal text reports also show the count, mean, and median active duration for top-level human-initiated turns. Those duration statistics use minutes.

## Install

Install the published package with either tool:

```bash
uv tool install codex-agent-hour-tracker
codex-agent-hour-tracker --share
```

```bash
pipx install codex-agent-hour-tracker
codex-agent-hour-tracker --share
```

The `agent-hours` command is also installed as a short alias. The CLI reads the local Codex session directory by default. Use `--sessions-dir PATH` to select another directory.

## Custom reports

The default report is text. Explicit `--start` and `--end` values define an inclusive local-calendar date range:

```bash
mkdir -p reports
agent-hours --start 2025-01-01 --end 2025-01-30 \
  > reports/january-summary.txt
```

CSV output has one row per calendar day. Save generated reports only under the ignored `reports/` directory:

```bash
agent-hours --start 2025-01-01 --end 2025-01-30 --format csv \
  > reports/january-summary.csv
```

Git ignores the `reports/` directory. Do not redirect generated output to tracked files.

## Default window and methodology

The canonical Archive Score and a report without date bounds cover the 30 most recent completed local calendar days ending yesterday. Calendar days include days with zero use. If today is March 1, the window is January 30 through February 28.

The scanner bounds each event gap at 30 minutes and uses bounded duration fallbacks. It attributes turns to their payload `started_at` date, excludes batch files, and deduplicates turns globally. Root-only statistics classify only sessions with a `cli`, `vscode`, or `user` source; delegated, unknown, malformed, or conflicting sources are excluded from that sample while remaining in non-batch all-turn totals. See the [methodology](docs/methodology.md) for exact rules and limitations.

## Privacy boundary

`--share` emits intentionally sanitized aggregate output and is the safe default for sharing. The card contains counts, durations, and dates, so sharing it remains a deliberate aggregate disclosure.

CSV and full text reports contain semi-sensitive activity patterns. Do not commit or share those files accidentally. Keep generated files under the ignored `reports/` directory and review any output before distribution.

The scanner retains compact timing metadata only. It does not decode conversation content or send session data over the network. Diagnostics are separate from report output and may include counts and, outside `--share`, bounded local file paths.

## Optional Codex skill

The optional Codex skill is available with this repository under [`skills/codex-agent-hour-tracker/`](https://github.com/tim-osterhus/codex-agent-hour-tracker/tree/main/skills/codex-agent-hour-tracker/). Use the repository path when you want the tracker workflow in Codex.

## Development and tests

The package requires Python 3.11 or newer. From a source checkout, run the synthetic test suite with:

```bash
uv run python -m unittest discover -s tests -v
```

The command exposes `agent-hours` and `codex-agent-hour-tracker` entry points. Packaging metadata and the test suite are the source of truth for supported commands.

Read the [security policy](SECURITY.md), [methodology](docs/methodology.md), and [MIT license](LICENSE) before deploying or sharing reports.
