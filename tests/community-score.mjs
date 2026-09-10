import test from "node:test";
import assert from "node:assert/strict";
import { parseScore } from "../community/score.js";

const NOW = Date.parse("2026-09-09T12:00:00.000Z");
const validScore = () => ({
  schema: "agent-hours-score",
  schema_version: 1,
  tracker_version: "0.2.0",
  methodology_version: "1",
  window: {
    start: "2026-08-10",
    end: "2026-09-08",
    days: 30,
    timezone: "UTC",
  },
  scope: "interactive-only",
  metrics: {
    agent_hours_per_day: 48,
    total_agent_hours: 1440,
    peak_day_agent_hours: 72,
    completed_turns: 300,
    active_days: 30,
  },
  leverage: null,
});

test("server parser accepts a bounded score with parallel-use hours above 24/day", () => {
  const parsed = parseScore(JSON.stringify(validScore()), { now: NOW });
  assert.equal(parsed.metrics.agent_hours_per_day, 48);
  assert.equal(parsed.leverage, null);
});

test("server parser rejects leverage and unknown fields before persistence", () => {
  const leveraged = validScore();
  leveraged.leverage = {
    ratio: 2,
    human_hours: 720,
    basis: "reported-period",
    human_hours_per_week: null,
  };
  assert.throws(() => parseScore(JSON.stringify(leveraged), { now: NOW }));

  const extra = validScore();
  extra.metrics.daily = [];
  assert.throws(() => parseScore(JSON.stringify(extra), { now: NOW }));
});

test("server parser rejects malformed, oversized, future, and stale windows", () => {
  assert.throws(() => parseScore("{"));
  assert.throws(() => parseScore("x".repeat(32769), { now: NOW }));

  const future = validScore();
  future.window.start = "2026-08-12";
  future.window.end = "2026-09-10";
  assert.throws(() => parseScore(JSON.stringify(future), { now: NOW }));

  const stale = validScore();
  stale.window.start = "2026-07-28";
  stale.window.end = "2026-08-26";
  assert.throws(() => parseScore(JSON.stringify(stale), { now: NOW }));
});

test("server parser enforces finite numeric ceilings and exact score fields", () => {
  for (const mutate of [
    (score) => (score.metrics.agent_hours_per_day = -1),
    (score) => (score.metrics.total_agent_hours = 1e12),
    (score) => (score.metrics.completed_turns = 1.5),
    (score) => (score.metrics.active_days = 31),
    (score) => (score.scope = "private"),
    (score) => (score.window.timezone = "<script>"),
    (score) => (score.window.days = 29),
  ]) {
    const score = validScore();
    mutate(score);
    assert.throws(() => parseScore(JSON.stringify(score), { now: NOW }));
  }
});
