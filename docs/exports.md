# Export contracts

Agent Hours has two JSON formats. They serve different purposes and are not interchangeable.

## Public score: `agent-hours-score`, schema version 1

Create with `agent-hours --share --format json`. The CLI fixes the window to 30 completed calendar days ending yesterday.

| Field | Contents |
| --- | --- |
| `schema`, `schema_version` | Format identity and version |
| `tracker_version`, `methodology_version` | Software and timing-rule versions |
| `window` | `start`, `end`, `days`, and IANA `timezone` |
| `scope` | `interactive-only` or `including-exec` |
| `metrics` | `agent_hours_per_day`, `total_agent_hours`, `peak_day_agent_hours`, `completed_turns`, `active_days` |
| `leverage` | `null`, or `ratio`, `human_hours`, `basis`, and `human_hours_per_week` |

Leverage basis is `reported-period` or `estimated-weekly`. Reported-period leverage has a null weekly-hours field. Numeric values are finite and nonnegative. Supplied human hours are positive.

The schema excludes daily rows, machine labels, account details, paths, identifiers, and diagnostics. The browser rejects private exports and unexpected fields. Imported scores are self-reported, not independently verified. A user can edit JSON, so these cards do not establish leaderboard eligibility or attest to an account's activity.

Pasting a score does not submit it to a server. PNG/SVG generation uses the same local renderer as the card preview. Exported images disclose the fields shown on the preview.

## Private archive: `agent-hours-archive`, schema version 1

Create with `agent-hours --export reports/machine.agent-hours-private.json --label machine`. Export collects available timing observations across the selected directories, including exec/batch observations. It is not a date-bounded public score.

The archive contains a methodology version, optional user label, compact observations, and diagnostics. Each observation includes a deterministic hash of the typed turn identifier, start timestamp, duration, timing usability, and source class. It contains no conversation payloads or raw turn identifiers.

Retaining the best observation per source class lets a later merge apply source selection before deduplication. It also preserves the independent human-root sample. The merge rejects incompatible versions and malformed data rather than silently reinterpreting them.

Precise timestamps and persistent hashes reveal activity patterns and permit correlation between exports. Hashing does not make this public-safe. Transfer archives only between machines or people you intend to grant this access. Store them under ignored `reports/` paths. Never paste a private archive into the public share studio.

## Reproduction

Use the same methodology, scope, date bounds, timezone, and input archives to reproduce a report. Individual input subtotals may overlap. Use the globally deduplicated combined total. Session histories that have been removed or rewritten can change later measurements.
