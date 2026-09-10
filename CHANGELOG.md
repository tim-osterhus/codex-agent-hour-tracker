# Changelog

## Unreleased website update

- Put the local score card directly below the run command, with Upload and Preview tabs.
- Hide detailed explanations under Stats for Nerds by default.
- Add a separate, opt-in community scoreboard for self-reported scores.
- Add GitHub sign-in, bot checks, and five accepted posts per account in a rolling 30-day window.
- Keep local imports private until the user confirms a community post.
- Leave the Python package and timing methodology unchanged at 0.2.0.

## 0.2.0

- Rename the public metric to Agent-Hour Score. Keep its exact 30-completed-calendar-day definition.
- Combine multiple session directories and private exports with global turn deduplication.
- Add explicit exec inclusion and show measurement scope on reports and cards.
- Calculate optional Agent Leverage from reported period hours or an estimated weekly schedule.
- Add monthly summaries with partial-period denominators.
- Export versioned sanitized score JSON for local browser share-card generation.
- Redesign agenthours.dev with an interactive parallel-runtime explanation, leverage calculator, share studio, and dated benchmarks.
- Keep the existing timing methodology and human-root duration statistics.

## 0.1.2

- Document compaction compatibility and add regressions for retained timing metadata without conversation decoding.

## 0.1.1

- Add human-initiated top-level turn count, mean, and median active duration.

## 0.1.0

- Publish the local scanner, calendar-day reports, sanitized scorecard, optional skill, and static website.
