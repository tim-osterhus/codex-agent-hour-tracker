import test, { after, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { Miniflare } from "miniflare";
import { readFile } from "node:fs/promises";
import {
  deleteOwnScore,
  getPublicScores,
  getUserScores,
  quotaForUser,
  recordAcceptedScore,
} from "../community/db.js";

const NOW = Date.parse("2026-09-09T12:00:00.000Z");
const USER_ID = 42;
const schema = (await Promise.all(["0001_community.sql", "0002_provider_scores.sql"].map((file) => readFile(new URL(`../migrations/${file}`, import.meta.url), "utf8")))).join("\n").replaceAll("\n", " ");
let mf;
let db;

function score(scope = "interactive-only", hours = 48) {
  return {
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
    scope,
    metrics: {
      agent_hours_per_day: hours,
      total_agent_hours: hours * 30,
      peak_day_agent_hours: hours,
      completed_turns: 300,
      active_days: 30,
    },
    leverage: null,
  };
}

function scoreWithWindow({ timezone = "UTC", start = "2026-08-10", end = "2026-09-08" } = {}) {
  const value = score();
  value.window.timezone = timezone;
  value.window.start = start;
  value.window.end = end;
  return value;
}

async function add(requestId, value = score(), now = NOW, githubId = USER_ID, login = "alice") {
  return recordAcceptedScore(db, {
    githubId,
    login,
    requestId,
    score: value,
    now,
  });
}

before(async () => {
  mf = new Miniflare({
    workers: [
      {
        config: {
          name: "community-test",
          type: "worker",
          compatibilityDate: "2026-09-09",
          env: {
            COMMUNITY_DB: {
              type: "d1",
              id: `community-${crypto.randomUUID()}`,
            },
          },
          manifest: {
            mainModule: "index.js",
            modules: {
              "index.js": { type: "esm", contents: "export default {};" },
            },
          },
        },
      },
    ],
    logRequests: false,
    telemetry: { enabled: false },
  });
  db = await mf.getD1Database("COMMUNITY_DB");
  await db.exec(schema);
  await db
    .prepare("INSERT INTO github_users (github_id, login, created_at, updated_at) VALUES (?, ?, ?, ?)")
    .bind(USER_ID, "alice", new Date(NOW).toISOString(), new Date(NOW).toISOString())
    .run();
});

beforeEach(async () => {
  await db.batch([
    db.prepare("DELETE FROM sessions"),
    db.prepare("DELETE FROM oauth_states"),
    db.prepare("DELETE FROM community_scores"),
    db.prepare("DELETE FROM quota_events"),
    db.prepare("DELETE FROM rate_buckets"),
    db.prepare("DELETE FROM github_users"),
    db
      .prepare("INSERT INTO github_users (github_id, login, created_at, updated_at) VALUES (?, ?, ?, ?)")
      .bind(USER_ID, "alice", new Date(NOW).toISOString(), new Date(NOW).toISOString()),
  ]);
});

after(async () => {
  await mf.dispose();
});

test("community migration creates private ledger, current scores, throttle, and quota trigger", async () => {
  const rows = await db
    .prepare("SELECT name, type FROM sqlite_master WHERE name IN ('quota_events', 'community_scores', 'rate_buckets', 'quota_cap_before_insert') ORDER BY name")
    .all();
  assert.deepEqual(rows.results, [
    { name: "community_scores", type: "table" },
    { name: "quota_cap_before_insert", type: "trigger" },
    { name: "quota_events", type: "table" },
    { name: "rate_buckets", type: "table" },
  ]);
});

test("five accepted posts are shared across scopes and the sixth is rejected atomically", async () => {
  const results = await Promise.all(
    Array.from({ length: 6 }, (_, index) =>
      add(
        `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
        score(index % 2 ? "including-exec" : "interactive-only", 48 + index),
      ),
    ),
  );
  assert.equal(results.filter((result) => result.status === "accepted").length, 5);
  assert.equal(results.filter((result) => result.status === "quota_exceeded").length, 1);
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 5);
  assert.equal((await quotaForUser(db, USER_ID, NOW)).remaining, 0);
});

test("an event exactly thirty days old expires at the boundary", async () => {
  const old = NOW - 30 * 24 * 60 * 60 * 1000;
  const historical = await add("00000000-0000-4000-8000-000000000101", score(), old);
  assert.equal(historical.status, "accepted");
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 0);
  const fresh = await add("00000000-0000-4000-8000-000000000102", score(), NOW);
  assert.equal(fresh.status, "accepted");
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 1);
});

test("the fourth event exposes the next rolling quota slot", async () => {
  for (let index = 0; index < 4; index += 1) {
    await add(`00000000-0000-4000-8000-00000000015${index}`, score(), NOW - index * 1000);
  }
  const quota = await quotaForUser(db, USER_ID, NOW);
  assert.equal(quota.used, 4);
  assert.equal(quota.remaining, 1);
  assert.equal(quota.next_slot_at, new Date(NOW - 3_000 + 30 * 24 * 60 * 60 * 1000).toISOString());
});

test("successful request replay is idempotent while a different payload conflicts", async () => {
  const requestId = "00000000-0000-4000-8000-000000000201";
  const first = await add(requestId, score("interactive-only", 50));
  const replay = await add(requestId, score("interactive-only", 50));
  const conflict = await add(requestId, score("interactive-only", 51));
  assert.equal(first.status, "accepted");
  assert.equal(replay.status, "replayed");
  assert.equal(conflict.status, "request_conflict");
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 1);
});

test("deleting the current score does not refund the private quota ledger", async () => {
  await add("00000000-0000-4000-8000-000000000301", score());
  await deleteOwnScore(db, { githubId: USER_ID, scope: "interactive-only" });
  assert.deepEqual(await getUserScores(db, USER_ID), []);
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 1);
});

test("account and scope isolation remains bounded and stable-tie sorted", async () => {
  await add("00000000-0000-4000-8000-000000000401", score("interactive-only", 54));
  await add("00000000-0000-4000-8000-000000000402", score("including-exec", 54));
  await add("00000000-0000-4000-8000-000000000403", score("interactive-only", 54), NOW, 77, "bob");
  const own = await getUserScores(db, USER_ID);
  assert.deepEqual(own.map(({ login, score: value }) => [login, value.scope]), [
    ["alice", "including-exec"],
    ["alice", "interactive-only"],
  ]);
  const board = await getPublicScores(db, "interactive-only", { now: NOW, limit: 50 });
  assert.deepEqual(board.map(({ login }) => login), ["alice", "bob"]);
  assert.ok(board.every((row) => Object.keys(row).sort().join(",") === "login,score,updated_at"));
});

test("board eligibility expires at declared-timezone midnight instead of a UTC approximation", async () => {
  await add(
    "00000000-0000-4000-8000-000000000501",
    scoreWithWindow({ timezone: "America/Los_Angeles", start: "2026-08-03", end: "2026-09-01" }),
  );
  const expires = Date.parse("2026-09-10T07:00:00.000Z");
  assert.equal((await getPublicScores(db, "interactive-only", { now: expires - 1 })).length, 1);
  assert.equal((await getPublicScores(db, "interactive-only", { now: expires })).length, 0);
});

test("board eligibility handles a declared midnight skipped by a DST transition", async () => {
  await add(
    "00000000-0000-4000-8000-000000000502",
    scoreWithWindow({ timezone: "America/Santiago", start: "2026-07-30", end: "2026-08-28" }),
  );
  // Chile advances clocks at local midnight on this date; the first instant
  // with the declared expiry date is 01:00 local, not the nonexistent 00:00.
  const expires = Date.parse("2026-09-06T04:00:00.000Z");
  assert.equal((await getPublicScores(db, "interactive-only", { now: expires - 1 })).length, 1);
  assert.equal((await getPublicScores(db, "interactive-only", { now: expires })).length, 0);
});

const providerScore = (provider, hours = 48) => ({ ...score("interactive-only", hours), schema_version: 2, provider });

test("provider boards, replacement and deletion isolate accounts, scopes and providers", async () => {
  await add("provider-codex", score("interactive-only", 103.02));
  await add("provider-claude", providerScore("claude", 20));
  await add("provider-all", providerScore("all", 123.02));
  await add("provider-claude-replace", providerScore("claude", 21));
  await add("provider-bob", providerScore("claude", 25), NOW, 77, "bob");
  assert.equal((await getUserScores(db, USER_ID)).length, 3);
  for (const [provider, hours] of [["codex", 103.02], ["claude", 25], ["all", 123.02]]) {
    const board = await getPublicScores(db, "interactive-only", { now: NOW, provider });
    assert.equal(board[0].score.metrics.agent_hours_per_day, hours);
    assert.ok(board.every(({ score }) => (score.provider ?? "codex") === provider));
  }
  await deleteOwnScore(db, { githubId: USER_ID, scope: "interactive-only", provider: "claude" });
  assert.equal((await getUserScores(db, USER_ID)).length, 2);
  assert.deepEqual((await getPublicScores(db, "interactive-only", { now: NOW, provider: "claude" })).map(({ login }) => login), ["bob"]);
  assert.equal((await getPublicScores(db, "interactive-only", { now: NOW }))[0].score.metrics.agent_hours_per_day, 103.02);
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 4);
});

test("provider writes share the existing atomic five-post quota and cannot cross-replay", async () => {
  const first = providerScore("claude");
  assert.equal((await add("same-provider-request", first)).status, "accepted");
  assert.equal((await add("same-provider-request", first)).status, "replayed");
  assert.equal((await add("same-provider-request", providerScore("all"))).status, "request_conflict");
  const results = await Promise.all(["codex", "claude", "all", "codex", "all"].map((provider, i) => add(`mixed-provider-${i}`, providerScore(provider))));
  assert.equal(results.filter(({ status }) => status === "accepted").length, 4);
  assert.equal(results.filter(({ status }) => status === "quota_exceeded").length, 1);
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 5);
});

test("expand-only migration preserves stored legacy Codex rows and old writer SQL", async () => {
  await add("legacy-before-upgrade", score("interactive-only", 103.02));
  const before = await db.prepare("SELECT * FROM community_scores").first();
  await db.exec("DROP TABLE community_provider_scores");
  const upgrade = await readFile(new URL("../migrations/0002_provider_scores.sql", import.meta.url), "utf8");
  await db.exec(upgrade.replaceAll("\n", " "));
  assert.deepEqual(await db.prepare("SELECT * FROM community_scores").first(), before);
  // The old deployment still writes exactly these original columns and conflict target.
  await db.prepare("INSERT INTO quota_events (request_id, github_id, scope, score_digest, accepted_at) VALUES (?, ?, ?, ?, ?)").bind("old-writer-request", USER_ID, "interactive-only", "legacy-digest", NOW).run();
  await db.prepare("INSERT INTO community_scores (github_id, scope, login, score_json, score_digest, window_end, window_timezone, eligible_until, agent_hours_per_day, updated_at, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(github_id, scope) DO UPDATE SET request_id = excluded.request_id")
    .bind(...[before.github_id, before.scope, before.login, before.score_json, before.score_digest, before.window_end, before.window_timezone, before.eligible_until, before.agent_hours_per_day, before.updated_at, "old-writer-request"]).run();
  assert.equal((await getPublicScores(db, "interactive-only", { now: NOW }))[0].score.metrics.agent_hours_per_day, 103.02);
  assert.equal((await add("new-after-upgrade", providerScore("claude"))).status, "accepted");
  assert.equal((await quotaForUser(db, USER_ID, NOW)).used, 3);
});
