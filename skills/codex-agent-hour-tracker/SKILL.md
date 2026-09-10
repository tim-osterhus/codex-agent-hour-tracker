---
name: codex-agent-hour-tracker
description: Measure local Codex agent-hours, merge machine exports, and produce sanitized Agent-Hour Scores.
---

# Codex Agent Hour Tracker

Use the installed `agent-hours` CLI, or `uvx codex-agent-hour-tracker`. The package performs the scan. Never read, print, summarize, or upload raw Codex session records.

## Measurement

- The Agent-Hour Score is mean cumulative agent-hours across exactly the 30 most recent completed local calendar days ending yesterday, including zero-use days.
- Overlapping turns count independently. Runtime does not establish productivity, work quality, or time saved.
- Default scope is `interactive-only`. Explain that this excludes exec/batch files but includes non-batch unknown sources.
- Use `--include-exec` when the user wants exec/batch activity included. Keep the scope visible when comparing reports.
- Human-initiated top-level count, mean, and median describe only the recognized root sample.
- Use an explicit common `--timezone` for comparisons across machines.

## Profiles and machines

- Repeat `--sessions-dir PATH` for multiple local Codex profiles.
- When the user asks for multiple-machine totals, use `--export reports/NAME.agent-hours-private.json --label NAME` on each authorized machine.
- Transfer only those private timing exports through an authorized channel. Never transfer raw session data.
- Merge with repeated `--merge FILE`. Let the package deduplicate identifiers; never add separately calculated totals and assume there is no overlap.
- Private exports contain precise timing metadata and persistent hashed identifiers. Keep them private under ignored `reports/` paths.
- Do not inspect credentials or infer account attribution. Accounts sharing a directory cannot be separated reliably.

## Reports and human hours

- Use `--monthly` for monthly text summaries and explicit `--start` / `--end` for an inclusive custom range.
- Full text and CSV reports reveal day-level activity patterns. Save only within the user's requested scope, using an ignored or explicitly chosen private location.
- Agent Leverage is total agent runtime divided by human hours for the same period.
- `--human-hours TOTAL` uses reported period hours; `--human-hours-per-week WEEKLY` uses an explicitly labeled prorated estimate.
- Never invent human hours from prompt timestamps or drop low-use calendar days to improve a score.

## Sharing

- `--share` produces the canonical aggregate text card. `--share --format json` produces sanitized public score JSON for the website.
- Custom date bounds are unavailable with `--share`. Do not relabel a custom report as a canonical score.
- Only sanitized public score output may enter the website's share studio. Never paste a private merge export.
- Public output omits daily rows, machine labels, paths, identifiers, and diagnostics. Its aggregate numbers, dates, timezone, and optional human-hours assumptions are still an intentional disclosure.
- A request to calculate or explain a result does not authorize publishing it. Publish only when the user requests that disclosure and destination.
- Imported share cards are self-reported, not verified. Benchmarks are dated contextual references, not personal percentiles.
