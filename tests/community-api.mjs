import test, { after, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { Miniflare } from "miniflare";
import { readFile } from "node:fs/promises";
import { createSession } from "../community/auth.js";
import { routeCommunity } from "../community/api.js";

const NOW = Date.parse("2026-09-09T12:00:00.000Z");
const USER_ID = 42;
const schema = (await readFile(new URL("../migrations/0001_community.sql", import.meta.url), "utf8")).replaceAll("\n", " ");
let mf;
let db;
let env;
let session;

const validScore = (scope = "interactive-only", hours = 48) => ({
  schema: "agent-hours-score",
  schema_version: 1,
  tracker_version: "0.2.0",
  methodology_version: "1",
  window: { start: "2026-08-10", end: "2026-09-08", days: 30, timezone: "UTC" },
  scope,
  metrics: {
    agent_hours_per_day: hours,
    total_agent_hours: hours * 30,
    peak_day_agent_hours: hours,
    completed_turns: 300,
    active_days: 30,
  },
  leverage: null,
});

const successTurnstile = async (url) => {
  assert.equal(url, "https://challenges.cloudflare.com/turnstile/v0/siteverify");
  return new Response(JSON.stringify({ success: true, hostname: "agenthours.dev", action: "community_post" }), {
    headers: { "Content-Type": "application/json" },
  });
};

function request(path, { method = "GET", body, cookie, csrf, origin = "https://agenthours.dev", extraHeaders = {} } = {}) {
  const headers = new Headers(extraHeaders);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (cookie) headers.set("Cookie", cookie);
  if (csrf) headers.set("X-CSRF-Token", csrf);
  if (origin !== undefined && origin !== null) headers.set("Origin", origin);
  return new Request(`https://agenthours.dev/api/community${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function sessionHeaders() {
  return {
    cookie: `__Host-community_session=${session.token}`,
    csrf: session.csrf,
  };
}

async function call(req, options = {}) {
  return routeCommunity(req, env, { now: NOW, fetchImpl: successTurnstile, ...options });
}

before(async () => {
  mf = new Miniflare({
    workers: [{
      config: {
        name: "community-api-test",
        type: "worker",
        compatibilityDate: "2026-09-09",
        env: { COMMUNITY_DB: { type: "d1", id: `community-api-${crypto.randomUUID()}` } },
        manifest: {
          mainModule: "index.js",
          modules: { "index.js": { type: "esm", contents: "export default {};" } },
        },
      },
    }],
    logRequests: false,
    telemetry: { enabled: false },
  });
  db = await mf.getD1Database("COMMUNITY_DB");
  await db.exec(schema);
});

beforeEach(async () => {
  await db.batch([
    db.prepare("DELETE FROM sessions"),
    db.prepare("DELETE FROM oauth_states"),
    db.prepare("DELETE FROM community_scores"),
    db.prepare("DELETE FROM quota_events"),
    db.prepare("DELETE FROM rate_buckets"),
    db.prepare("DELETE FROM github_users"),
  ]);
  env = {
    COMMUNITY_DB: db,
    SITE_ORIGIN: "https://agenthours.dev",
    SUBMISSIONS_ENABLED: "true",
    GITHUB_CLIENT_ID: "public-client",
    GITHUB_CLIENT_SECRET: "oauth-secret-0123456789abcdef",
    SESSION_SECRET: "session-secret-0123456789abcdef0",
    TURNSTILE_SECRET_KEY: "turnstile-secret-0123456789abcdef",
    TURNSTILE_SITE_KEY: "site-key",
  };
  session = await createSession(db, { githubId: USER_ID, login: "alice", now: NOW });
});

after(async () => {
  await mf.dispose();
});

test("config fails closed unless the submission flag and every binding/secret are present", async () => {
  const enabled = await call(request("/config"));
  assert.deepEqual(await enabled.json(), { enabled: true, turnstile_site_key: "site-key" });

  env.SUBMISSIONS_ENABLED = "false";
  const disabled = await call(request("/config"));
  assert.deepEqual(await disabled.json(), { enabled: false, turnstile_site_key: null });
  assert.equal(disabled.headers.get("Access-Control-Allow-Origin"), null);

  delete env.SESSION_SECRET;
  const missingSecret = await call(request("/config"));
  assert.deepEqual(await missingSecret.json(), { enabled: false, turnstile_site_key: null });
});

test("anonymous session never exposes quota, scores, or identity details", async () => {
  const response = await call(request("/session"));
  assert.deepEqual(await response.json(), {
    authenticated: false,
    user: null,
    csrf_token: null,
    quota: null,
    scores: [],
  });
});

test("mutations require exact same-origin and CSRF protection", async () => {
  const body = { score: validScore(), turnstile_token: "token", request_id: "00000000-0000-4000-8000-000000000601" };
  for (const options of [
    { ...sessionHeaders(), origin: null },
    { ...sessionHeaders(), origin: "https://evil.example" },
    { cookie: sessionHeaders().cookie, csrf: "wrong-csrf-token", origin: "https://agenthours.dev" },
  ]) {
    const response = await call(request("/scores", { method: "POST", body, ...options }));
    assert.equal(response.status, 403);
    assert.deepEqual(Object.keys(await response.json()), ["error", "message"]);
  }
});

test("posting validates server-side score and Turnstile, then returns quota", async () => {
  let calls = 0;
  const response = await call(
    request("/scores", {
      method: "POST",
      body: { score: validScore(), turnstile_token: "token", request_id: "00000000-0000-4000-8000-000000000602" },
      ...sessionHeaders(),
    }),
    {
      fetchImpl: async (...args) => {
        calls += 1;
        return successTurnstile(...args);
      },
    },
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    ok: true,
    quota: { used: 1, remaining: 4, next_slot_at: null },
  });
  assert.equal(calls, 1);
});

test("request replay is idempotent before Turnstile and a changed payload is a conflict", async () => {
  const body = { score: validScore(), turnstile_token: "token", request_id: "00000000-0000-4000-8000-000000000603" };
  await call(request("/scores", { method: "POST", body, ...sessionHeaders() }));
  let calls = 0;
  const replay = await call(request("/scores", { method: "POST", body: { ...body, turnstile_token: "expired" }, ...sessionHeaders() }), {
    fetchImpl: async () => {
      calls += 1;
      return new Response(JSON.stringify({ success: false }), { headers: { "Content-Type": "application/json" } });
    },
  });
  assert.equal(replay.status, 200);
  assert.equal(calls, 0);

  const conflict = await call(request("/scores", { method: "POST", body: { ...body, score: validScore("interactive-only", 49) }, ...sessionHeaders() }));
  assert.equal(conflict.status, 409);
  assert.equal((await conflict.json()).error, "request_id_conflict");
});

test("Turnstile hostname and action are checked, and malformed/oversized bodies are bounded", async () => {
  const invalidTurnstile = await call(
    request("/scores", {
      method: "POST",
      body: { score: validScore(), turnstile_token: "token", request_id: "00000000-0000-4000-8000-000000000604" },
      ...sessionHeaders(),
    }),
    { fetchImpl: async () => new Response(JSON.stringify({ success: true, hostname: "evil.example", action: "community_post" })) },
  );
  assert.equal(invalidTurnstile.status, 400);
  assert.equal((await invalidTurnstile.json()).error, "turnstile_invalid");

  const malformed = await call(request("/scores", { method: "POST", body: { score: validScore() }, ...sessionHeaders() }));
  assert.equal(malformed.status, 400);

  const oversized = new Request("https://agenthours.dev/api/community/scores", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: "https://agenthours.dev",
      Cookie: `__Host-community_session=${session.token}`,
      "X-CSRF-Token": session.csrf,
      "Content-Length": "70000",
    },
    body: "x",
  });
  const bounded = await call(oversized);
  assert.equal(bounded.status, 413);
});

test("session and board expose only allowlisted public fields, and delete keeps quota consumed", async () => {
  const body = { score: validScore(), turnstile_token: "token", request_id: "00000000-0000-4000-8000-000000000605" };
  await call(request("/scores", { method: "POST", body, ...sessionHeaders() }));
  const sessionResponse = await call(request("/session", { ...sessionHeaders() }));
  const sessionJson = await sessionResponse.json();
  assert.equal(sessionJson.user.login, "alice");
  assert.equal(sessionJson.quota.used, 1);
  assert.deepEqual(Object.keys(sessionJson.scores[0]).sort(), ["login", "score", "updated_at"]);
  assert.equal(sessionJson.scores[0].score.leverage, null);

  const board = await call(request("/scores?scope=interactive-only"));
  assert.deepEqual(Object.keys((await board.json()).scores[0]).sort(), ["login", "score", "updated_at"]);

  const deleted = await call(request("/scores", { method: "DELETE", body: { scope: "interactive-only" }, ...sessionHeaders() }));
  assert.deepEqual(await deleted.json(), { ok: true });
  const afterDelete = await call(request("/session", { ...sessionHeaders() }));
  assert.equal((await afterDelete.json()).quota.used, 1);
});

test("missing database or secrets returns a disabled error instead of permissive success", async () => {
  const missingDb = await routeCommunity(request("/scores?scope=interactive-only"), { ...env, COMMUNITY_DB: undefined }, { now: NOW });
  assert.equal(missingDb.status, 503);
  env.SESSION_SECRET = "";
  const disabledPost = await call(request("/scores", { method: "POST", body: {}, ...sessionHeaders() }));
  assert.equal(disabledPost.status, 503);
});

test("an unconfigured session read is anonymous and does not prune or mutate the database", async () => {
  const before = Number((await db.prepare("SELECT COUNT(*) AS count FROM sessions").first()).count);
  env.SESSION_SECRET = "";
  const response = await call(request("/session", { ...sessionHeaders() }));
  assert.deepEqual(await response.json(), {
    authenticated: false,
    user: null,
    csrf_token: null,
    quota: null,
    scores: [],
  });
  const after = Number((await db.prepare("SELECT COUNT(*) AS count FROM sessions").first()).count);
  assert.equal(after, before);
});
