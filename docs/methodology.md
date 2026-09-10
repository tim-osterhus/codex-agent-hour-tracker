# Methodology

Agent Hour Tracker scans local Codex session JSONL files and retains completed-turn timing metadata. The implementation and tests in `src/agent_hour_tracker/` and `tests/` define the behavior described here.

## Timing

For an open turn, each valid top-level event timestamp advances active time from the previous timestamp. Each event gap contributes nonnegative elapsed time capped at 30 minutes. The cap prevents an idle gap from inflating active runtime.

When the event timeline has no positive duration, the scanner uses the completion's `duration_ms`, bounded to 30 minutes. If that field is unavailable, it uses `completed_at - started_at`, also bounded to 30 minutes. These are fallback durations. Diagnostics count event-timing fallbacks, and timestamp subtraction also increments duration fallbacks.

## Calendar-day attribution

The payload `started_at` value supplies the turn start timestamp. The selected IANA timezone converts that timestamp to a local calendar date. The full turn duration belongs to that start date. The tracker does not split one turn across midnight.

An explicit `--start` or `--end` date defines an inclusive range. Turns whose local start date falls outside the range do not contribute. The metrics builder creates one row for every date in the range, including zero-use days. Calendar-day means, medians, percentiles, and the daily histogram use those zero-use rows.

## Agent-Hour Score window

The canonical Agent-Hour Score and an unbounded report use the 30 most recent completed local calendar days ending yesterday. The range is inclusive. If today is March 1 in a non-leap year, the range is January 30 through February 28. `--share` always uses this window and rejects explicit date bounds. It supports text or sanitized JSON output. The old name was Archive Score.

Root-turn duration statistics use the same inclusive local-start-date range and selected timezone. A root sample includes valid zero-duration turns. An empty root sample reports count 0 and mean and median 0.00 minutes. The normal text report shows the sample count and its mean and median active durations in minutes. These statistics do not change the all-turn daily rows or agent-hour totals.

## Session selection and exec inclusion

The default `interactive-only` scope includes non-batch turns. A root session has a source string of `cli`, `vscode`, or `user`. A delegated session has a direct source mapping with a `subagent` key. The scanner classifies a session file as batch when session metadata uses `exec`, `batch`, or `codex_exec`. A direct source mapping with an `exec` or `batch` key also marks the file as batch. Batch wins if present in a file.

`--include-exec` selects the `including-exec` scope and includes batch observations in runtime totals. Exec records never enter the human-root sample. The default label describes a source filter, not proven human initiation of every included turn. Some human-directed remote activity uses exec sources. Classification cannot distinguish it from unattended scripts using the same source metadata.

Unknown, missing, malformed, and conflicting source evidence remains eligible for non-batch all-turn aggregation, but is excluded from root-only statistics. Mixed non-batch source evidence never enters the root sample. The normal report emits a bounded fingerprint label for an unknown source. Share diagnostics omit that label.

## Global deduplication and ranking

The tracker applies the selected source filter before deduplicating observations globally by turn identity across all input files, directories, and private exports. Private exports use SHA256 of a type-tagged turn identifier. One observation remains for each identity in the all-turn list. Root observations are deduplicated separately, so a delegated duplicate cannot hide a root observation in root-only statistics. The ranking uses event-timing usability first:

1. Prefer an observation with usable event timing.
2. Prefer the greater duration.
3. Prefer the earlier start timestamp when the first two values tie.

The scanner then sorts retained turns by start timestamp, turn-ID type and representation, and duration. Duplicate observations increment the duplicate diagnostic count.

## Compacted records

Codex can store compacted context in top-level `compacted` records and `event_msg` records whose payload type is `context_compacted`. The scanner does not treat either envelope as turn metadata and does not decode its payload. A valid top-level timestamp still advances every open turn under the standard event-gap rule.

Compaction remains compatible while the archive retains `task_started` and `task_complete` metadata. If a future archive format removes or replaces those records, the scanner will omit affected turns until that format is supported.

## Human-hours denominator and Agent Leverage

Agent Leverage equals cumulative agent runtime divided by human work hours for the same report period. `--human-hours` supplies the total for that period. `--human-hours-per-week` estimates it as weekly hours multiplied by the number of calendar days and divided by seven. The two flags are mutually exclusive. No human-hours assumption is applied by default.

All agent runtime in the period stays in the numerator, including runtime on human days off. The weekly estimate prorates an average schedule; it does not reconstruct actual weekdays or vacations. Prompt activity cannot establish human labor time. Runtime and leverage do not measure productivity, work quality, or time saved.

## Monthly summaries and multiple inputs

Monthly summaries group the report's daily rows and include zero-use days. Partial months show the actual covered date range and use its day count, not the full month's length. Input subtotals describe each supplied archive or directory independently. They may overlap and need not sum to the globally deduplicated total.

Machine labels are optional user-supplied descriptions. The tool does not read account credentials and cannot separate accounts sharing one session directory. See [export contracts](exports.md) for private transfer and public sharing boundaries.

## Benchmark context

The bundled registry records source, publication date, measurement period, population, units, and limitations. The website and package share these values.

OpenAI reports an aggregate ratio of 3.1 agent-workdays per human workday for its research organization in mid-August 2026. A personal denominator based on an assumed weekly schedule does not establish equivalent measurement. [September 6 source](https://openai.com/index/research-acceleration-view-inside-openai/)

The historical internal P99 reference exceeds 60 agent-hours per active day in June 2026. Its daily-active denominator differs from this tool's 30-calendar-day score. It does not establish a current external-user percentile. [June 25 source](https://openai.com/index/how-agents-are-transforming-work/)

## Diagnostics

The CLI reports malformed candidate lines and affected file counts. It reports incomplete turns, unmatched completions, duration fallbacks, event-timing fallbacks, duplicate observations, and excluded batch turns when those counts are nonzero. A normal report also lists unknown source fingerprints. `--share` keeps diagnostics aggregate-only and omits source labels and file paths.

## Limitations

- The scanner uses recognized top-level envelopes and bounded direct metadata scalars. It does not decode transcript, reasoning, base instructions, or tool payloads.

- The tracker assigns a turn's full duration to its local start date. Long turns can therefore make one day appear busier than the elapsed timeline suggests.

- The tracker sums overlapping turns independently. Agent-hours measure cumulative runtime, not wall-clock occupancy.

- The 30-minute event-gap cap and fallback durations are estimates when session timestamps are sparse or incomplete.

- Batch detection is file-level. One batch classification excludes all completed turns in that file under the default scope.

- Malformed records, incomplete turns, unmatched completions, and invalid metadata do not contribute to totals. Diagnostics expose counts, not the discarded content.
