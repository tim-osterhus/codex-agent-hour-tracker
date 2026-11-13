---
name: codex-agent-hour-tracker
description: Measure local Codex and Claude Code agent-hours, merge machine exports, and produce sanitized Agent-Hour Scores.
---

# Codex Agent Hour Tracker

Use the installed `agent-hours` CLI, or `uvx codex-agent-hour-tracker`. The package performs the scan. Never read, print, summarize, or upload raw Codex or Claude Code session records.

## Measurement

- The Agent-Hour Score is mean cumulative agent-hours across exactly the 30 most recent completed local calendar days ending yesterday, including zero-use days.
- Overlapping turns count independently. Runtime does not establish productivity, work quality, or time saved.
- Default scope is `interactive-only`. For Codex, this excludes exec/batch files but includes non-batch unknown sources. Claude native turns remain eligible under either scope.
- Use `--include-exec` when the user wants Codex exec/batch activity included. Keep the scope visible when comparing reports.
- Human-initiated top-level count, mean, and median describe only the recognized root sample.
- Use an explicit common `--timezone` for comparisons across machines.

## Claude Code

- Codex remains the default. Use `--provider claude` or `--provider all` for Claude or combined reports and public scores.
- Repeat `--claude-sessions-dir PATH` for explicit Claude project roots. Default discovery uses `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects`.
- Count only native main timers and completed foreground-agent timers. Child-native-only and async histories without completion timing are excluded. Never infer runtime or human initiation from transcript content.
- Combined totals add provider runtime, including overlap. Use provider subtotals rather than comparing combined totals with Codex benchmarks.
- Use `agent-hours --provider claude --share --format json` for a Claude public score, or `--provider all` for a combined score. Never relabel either as Codex.
- Claude main-session origin is unverified and can include SDK or headless activity. Claude and combined scores do not establish human-only activity.

## Profiles and machines

- Repeat `--sessions-dir PATH` for multiple local Codex profiles.
- When the user asks for multiple-machine totals, use `--provider all --export reports/NAME.agent-hours-private.json --label NAME` on each authorized machine.
- Transfer only those private timing exports through an authorized channel. Never transfer raw session data.
- Merge with `--provider all` and repeated `--merge FILE`. Let the package deduplicate identifiers; never add separately calculated totals and assume there is no overlap.
- Private exports contain precise timing metadata and persistent hashed identifiers. Keep them private under ignored `reports/` paths.
- Do not inspect credentials or infer account attribution. Accounts sharing a directory cannot be separated reliably.

## Reports and human hours

- Use `--monthly` for monthly text summaries and explicit `--start` / `--end` for an inclusive custom range.
- Full text and CSV reports reveal day-level activity patterns. Save only within the user's requested scope, using an ignored or explicitly chosen private location.
- Agent Leverage is total agent runtime divided by human hours for the same period.
- `--human-hours TOTAL` uses reported period hours; `--human-hours-per-week WEEKLY` uses an explicitly labeled prorated estimate.
- Never invent human hours from prompt timestamps or drop low-use calendar days to improve a score.

## Private account usage

- Use `agent-hours account-usage --label NAME` only when account token summaries or daily history are requested. This queries the existing ChatGPT-backed Codex login; API-key-only auth is unsupported. Never read credentials.
- Keep outputs private under ignored `reports/account-usage/` by default. Identity is fingerprinted. Use `--include-identity` only for explicitly requested plaintext identity and `--stdout` only for explicitly authorized secure capture. Never print daily history into a public result.
- Preserve nulls and sparse history. Bucket timezone, retention, and lifetime completeness are unknown. Do not infer lifetime coverage or treat missing days as zero.
- Account usage must never enter `--share`, website payloads, share cards, or the public scoreboard.

## Sharing

- `--share` produces the canonical aggregate text card. `--share --format json` produces sanitized public score JSON for the website.
- Codex public JSON retains schema v1. Claude and combined public JSON use schema v2 with a required provider. Combined public scores contain one aggregate.
- Custom date bounds are unavailable with `--share`. Do not relabel a custom report as a canonical score.
- Only sanitized public score output may enter the website's share studio. Never paste a private merge export.
- Public output omits daily rows, machine labels, paths, identifiers, and diagnostics. Its aggregate numbers, dates, timezone, and optional human-hours assumptions are still an intentional disclosure.
- A request to calculate or explain a result does not authorize publishing it. Publish only when the user requests that disclosure and destination.
- Imported share cards are self-reported, not verified. Benchmarks are dated contextual references, not personal percentiles.
- Community boards separate provider and session scope. Posting replaces only that provider/scope score. Five accepted posts per GitHub account in a rolling 30-day window apply across every board. Community posts omit leverage and human-hours assumptions.
