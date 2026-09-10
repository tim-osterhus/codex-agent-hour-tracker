import test from "node:test";
import assert from "node:assert/strict";
import * as score from "../site/score.mjs";

test("local score starts empty and a failed replacement clears the share target", () => {
  const state = new score.LocalScore();
  assert.equal(state.score, null);
  assert.equal(state.shareable, false);
  state.import(JSON.stringify(score.EXAMPLE));
  assert.equal(state.shareable, true);
  assert.throws(() => state.import('{"private":"data"}'));
  assert.equal(state.score, null);
  assert.equal(state.shareable, false);
});
test("preview combines agent count and hours and can never become a public score", () => {
  const state = new score.LocalScore();
  state.preview(3, 4, 40);
  assert.equal(state.score.metrics.agent_hours_per_day, 12);
  assert.equal(state.score.metrics.total_agent_hours, 360);
  assert.equal(state.shareable, false);
  assert.throws(() => state.handoff());
  state.preview(3, 4, null);
  assert.equal(state.score.leverage, null);
  assert.throws(() => state.preview(3, Infinity, null));
  assert.equal(state.score, null);
});
test("share handoff keeps only validated public fields and clears human hours", () => {
  const state = new score.LocalScore();
  state.import(
    JSON.stringify({
      ...score.EXAMPLE,
      leverage: {
        ratio: 3.72,
        human_hours: 100,
        basis: "reported-period",
        human_hours_per_week: null,
      },
    }),
  );
  const shared = JSON.parse(state.handoff());
  assert.equal(shared.leverage, null);
  assert.equal(state.score.leverage.human_hours, 100);
  assert.deepEqual(shared, score.EXAMPLE);
});
test("oversize files are rejected before their contents are read", async () => {
  const state = new score.LocalScore();
  state.import(JSON.stringify(score.EXAMPLE));
  let read = false;
  await assert.rejects(
    state.importFile({
      size: 32769,
      text() {
        read = true;
        return JSON.stringify(score.EXAMPLE);
      },
    }),
  );
  assert.equal(read, false);
  assert.equal(state.shareable, false);
});
test("quota warnings begin on fourth accepted post and use server release time", () => {
  assert.equal(
    score.quotaNotice({
      used: 3,
      remaining: 2,
      next_slot_at: "2026-10-01T12:00:00Z",
    }),
    "",
  );
  assert.match(
    score.quotaNotice({
      used: 4,
      remaining: 1,
      next_slot_at: "2026-10-01T12:00:00Z",
    }),
    /1 post left/,
  );
  assert.match(
    score.quotaNotice({
      used: 5,
      remaining: 0,
      next_slot_at: "2026-10-01T12:00:00Z",
    }),
    /2026-10-01T12:00:00Z/,
  );
});
