---
name: codex-agent-hour-tracker
description: Use when working with Codex tracker reports for agent-hours and Archive Scores.
---

# Codex Agent Hour Tracker

Use the tracker CLI for agent-hour measurements and report explanations. Do not handle the underlying session records yourself.

## Invocation

- Use `agent-hours` when that executable is installed. Otherwise use `uvx codex-agent-hour-tracker` with the same arguments.
- Include `--share` for an explicit public, screenshot-ready, or comparison request.
- For public, screenshot-ready, or comparison output, the only permitted artifact is the sanitized canonical `--share` card.
- Never open, read, summarize, upload, or print raw session JSONL or other raw files under the sessions directory. The CLI performs the bounded scan; do not inspect or recreate it manually. Never upload raw session files under any circumstance.

## Meaning and scope

- Explain that the Archive Score is the mean cumulative agent-hours per calendar day—not the total—across exactly the 30 most recent completed local calendar days ending yesterday, including zero-use days.
- The card also shows total agent-hours, peak-day hours, and completed-turn counts for context; those values are not the score.
- A single scanned archive produces an Archive Score. Never call it an account, operator, or person score.
- Aggregate across machines only when explicitly requested, and only from already-produced bounded outputs; never transfer or combine raw session data.

## Reports and authorization

- CSV exposes day-level activity and turn-count patterns. Treat full text and CSV reports as private activity records.
- Ask for permission before you save either a full text report or CSV, and save only to a user-approved path.
- Never publish, upload, or share an Archive Score card without an explicit user request. Never publish, upload, or share a full text report or CSV without an explicit user request. Never upload raw session files under any circumstance.
- Only an explicit user request authorizes publishing, uploading, or sharing; a request to calculate or explain a score alone is not permission to disclose it.

## Canonical sharing and custom ranges

- `--share` is canonical-only: it rejects explicit `--start`, `--end`, and `--format` values and uses the standard Archive Score window.
- If a public or screenshot request names custom dates, explain this constraint and offer the canonical sanitized card. Do not expose a custom or full report as public output.
- Treat a requested custom full report as private. Obtain explicit save or disclosure authorization and a user-approved path before saving or disclosing it.
