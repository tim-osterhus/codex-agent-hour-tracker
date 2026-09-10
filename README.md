# Agent Hours

Agent Hours is a local CLI that measures cumulative Codex agent-hours from completed turns.

See your runtime across profiles and machines, calculate optional Agent Leverage, and make a share card at [agenthours.dev](https://agenthours.dev).

```bash
uvx codex-agent-hour-tracker --share
```

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and a local Codex session archive. The package supports Python 3.11–3.14 on macOS, Linux, and Windows. Archive-derived runtime is an estimate: missing records and sparse timestamps affect coverage.

## Your Agent-Hour Score

The score is mean cumulative runtime across the 30 most recent completed local calendar days ending yesterday. Zero-use days count. Overlapping turns count independently: three agents running for one hour contribute three agent-hours.

If today is March 1 in a non-leap year, the window is January 30 through February 28. Custom date ranges produce reports, not canonical scores.

The default `interactive-only` scope excludes exec/batch files. It includes delegated turns and non-batch turns with uncertain source metadata. Use `--include-exec` to count exec/batch work as well. Every score states its scope.

## Install once

```bash
uv tool install codex-agent-hour-tracker
agent-hours --share
```

Or use pipx:

```bash
pipx install codex-agent-hour-tracker
codex-agent-hour-tracker --share
```

Both command names invoke the same tool. Upgrade an existing uv installation with `uv tool upgrade codex-agent-hour-tracker`.

## Calculate Agent Leverage

Supply your human hours for the entire report period:

```bash
agent-hours --share --human-hours 160
```

Or estimate them from a weekly schedule:

```bash
agent-hours --share --human-hours-per-week 40
```

Agent Leverage divides agent runtime by human hours over the same period. A weekly estimate uses `weekly hours × calendar days / 7`. The output identifies the assumption. The tool does not measure your human work time or infer it from prompt timestamps.

## Make a share card

```bash
agent-hours --share --format json
```

Upload or paste this sanitized aggregate JSON into the [share studio](https://agenthours.dev/#share). Preview and download a square or landscape PNG/SVG in your browser. Importing a score and making a card run locally. The website accepts only the public score schema.

`--share` is the safe default for sharing, but dates, counts, durations, timezone, and optional human-hours assumptions remain a deliberate aggregate disclosure. Review the card before posting it.

## Join the community scoreboard

The separate [community page](https://agenthours.dev/community/) shows self-reported scores. GitHub sign-in identifies the person posting, but does not verify their runtime. Scores can be edited or inflated.

After importing your score, choose **Share to community**. Review the public fields, sign in with GitHub, and confirm the post. Importing a file never posts it. The community score omits Agent Leverage and human-hours assumptions.

Each GitHub account can make five successful posts in a rolling 30-day window. Updating a score counts as a post. Deleting it does not refund a slot. The page warns you after your fourth post and shows when a slot opens.

The local card tool works without an account or community access. Hosting and privacy details are in the [community guide](docs/community.md).

## Profiles and machines

Repeat `--sessions-dir` to combine local profile directories:

```bash
agent-hours --sessions-dir PROFILE_A/sessions --sessions-dir PROFILE_B/sessions --share
```

For multiple machines, export private timing metadata on each machine:

```bash
mkdir -p reports
agent-hours --export reports/laptop.agent-hours-private.json --label laptop
```

Transfer those private exports using your own secure file-transfer method, then merge them:

```bash
agent-hours --merge reports/laptop.agent-hours-private.json \
  --merge reports/desktop.agent-hours-private.json --share --include-exec
```

The merge deduplicates turn identifiers before calculating the combined total. Private exports retain all source classes so you can choose the scope when merging. They contain precise timestamps and hashed turn identifiers. **Do not upload them to the website or commit them.** The tracker does not read account credentials or separate accounts that share a session directory.

## Daily and monthly reports

Explicit `--start` and `--end` values define an inclusive local-calendar range:

```bash
mkdir -p reports
agent-hours --start 2025-01-01 --end 2025-01-30 --monthly \
  > reports/january-summary.txt
agent-hours --start 2025-01-01 --end 2025-01-30 --format csv \
  > reports/january-summary.csv
```

Text reports include daily distribution, monthly summaries when requested, and human-initiated top-level turn count, mean, and median duration. CSV retains one row per calendar date. Partial-month averages use only the days in the requested range. Use `--timezone IANA_ZONE` when combining machines with different local timezones.

Full reports and CSVs reveal semi-sensitive activity patterns. Keep generated files in the ignored `reports/` directory and review any output before distribution.

## What the numbers can tell you

Agent-hours measure runtime, not useful output, human-equivalent labor, or time saved. Benchmarks are dated contextual references, not a personal ranking. See [benchmarks](https://agenthours.dev/benchmarks/) and the [methodology](docs/methodology.md) for populations, denominators, and timing rules.

The scanner retains compact timing metadata without decoding conversation, reasoning, or tool payloads. No session data leaves the machine through the CLI. Compacted records remain supported while completed-turn metadata survives.

## Development

```bash
uv run python -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
npm run build:check
python3 scripts/sync_benchmarks.py --check
```

The repository has a Python package in `src/agent_hour_tracker/`, synthetic tests in `tests/`, and browser assets in `site/`. The optional community API uses Pages Functions in `functions/`, application code in `community/`, and D1 migrations in `migrations/`. Node.js 24 runs the website development tools. Installing the Python CLI does not install or run the website backend.

The [export contracts](docs/exports.md) define private merge data and public score data separately. The package benchmark registry generates the website copy through `scripts/sync_benchmarks.py`.

The [optional Codex skill](https://github.com/tim-osterhus/codex-agent-hour-tracker/tree/main/skills/codex-agent-hour-tracker/) guides collection and sharing workflows. It is not required to install or run the tool.

Read the [changelog](CHANGELOG.md), [security policy](SECURITY.md), and [MIT license](LICENSE).
