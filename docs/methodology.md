# Methodology

Agent Hour Tracker scans local Codex session JSONL files and retains completed-turn timing metadata. The implementation and tests in `src/agent_hour_tracker/` and `tests/` define the behavior described here.

## Timing

For an open turn, each valid top-level event timestamp advances active time from the previous timestamp. Each event gap contributes nonnegative elapsed time capped at 30 minutes. The cap prevents an idle gap from inflating active runtime.

When the event timeline has no positive duration, the scanner uses the completion's `duration_ms`, bounded to 30 minutes. If that field is unavailable, it uses `completed_at - started_at`, also bounded to 30 minutes. These are fallback durations. Diagnostics count event-timing fallbacks, and timestamp subtraction also increments duration fallbacks.

## Calendar-day attribution

The payload `started_at` value supplies the turn start timestamp. The selected IANA timezone converts that timestamp to a local calendar date. The full turn duration belongs to that start date. The tracker does not split one turn across midnight.

An explicit `--start` or `--end` date defines an inclusive range. Turns whose local start date falls outside the range do not contribute. The metrics builder creates one row for every date in the range, including zero-use days. Calendar-day means, medians, percentiles, and the daily histogram use those zero-use rows.

## Archive Score window

The canonical Archive Score and an unbounded report use the 30 most recent completed local calendar days ending yesterday. The range is inclusive. If today is March 1, the range is January 30 through February 28. `--share` always uses this window and rejects explicit date bounds and report formats.

Root-turn duration statistics use the same inclusive local-start-date range and selected timezone. A root sample includes valid zero-duration turns. An empty root sample reports count 0 and mean and median 0.00 minutes. The normal text report shows the sample count and its mean and median active durations in minutes; these statistics do not change the all-turn daily rows or agent-hour totals.

## Session selection and batch exclusion

The scanner includes non-batch turns in the existing all-turn totals. A root session has a source string of `cli`, `vscode`, or `user`. A delegated session has a direct source mapping with a `subagent` key. The scanner classifies a session file as batch when its session metadata identifies the source strings `exec`, `batch`, or `codex_exec`, or a direct source mapping contains an `exec` or `batch` key. Batch wins if present and excludes every completed turn found in that file.

Unknown, missing, malformed, and conflicting source evidence remains eligible for non-batch all-turn aggregation, but is excluded from root-only statistics. Mixed non-batch source evidence never enters the root sample. The normal report emits a bounded fingerprint label for an unknown source. Share diagnostics omit that label.

## Global deduplication and ranking

The scanner excludes batch files before deduplicating observations globally by `turn_id` across all session files. One observation remains for each ID in the existing all-turn list. Root observations are deduplicated separately, after batch exclusion, so a delegated duplicate cannot hide a root observation in root-only statistics. The all-turn ranking is unchanged and uses event-timing usability first:

1. Prefer an observation with usable event timing.
2. Prefer the greater duration.
3. Prefer the earlier start timestamp when the first two values tie.

The scanner then sorts retained turns by start timestamp, turn-ID type and representation, and duration. Duplicate observations increment the duplicate diagnostic count.

## Diagnostics

The CLI reports malformed candidate lines and affected file counts. It reports incomplete turns, unmatched completions, duration fallbacks, event-timing fallbacks, duplicate observations, and excluded batch turns when those counts are nonzero. A normal report also lists unknown source fingerprints. `--share` keeps diagnostics aggregate-only and omits source labels and file paths.

## Limitations

- The scanner uses recognized top-level envelopes and bounded direct metadata scalars. It does not decode transcript, reasoning, base instructions, or tool payloads.

- The tracker assigns a turn's full duration to its local start date. Long turns can therefore make one day appear busier than the elapsed timeline suggests.

- The tracker sums overlapping turns independently. Agent-hours measure cumulative runtime, not wall-clock occupancy.

- The 30-minute event-gap cap and fallback durations are estimates when session timestamps are sparse or incomplete.

- Batch detection is file-level. One batch classification excludes all completed turns in that file.

- Malformed records, incomplete turns, unmatched completions, and invalid metadata do not contribute to totals. Diagnostics expose counts, not the discarded content.
