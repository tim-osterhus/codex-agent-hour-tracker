import test, { after, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Miniflare } from "miniflare";

import { routeCommunity } from "../community/api.js";
import { createSession } from "../community/auth.js";
import { sha256 } from "../community/crypto.js";

const NOW = Date.parse("2026-09-09T12:00:00.000Z");
const ORIGIN = "https://agenthours.dev";
const IP = "192.0.2.44";
const schema = await readFile(
  new URL("../migrations/0001_community.sql", import.meta.url),
  "utf8",
).then((value) => value.replaceAll("\n", " "));

let mf;
let db;
let env;

function validScore() {
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
    scope: "interactive-only",
    metrics: {
      agent_hours_per_day: 48,
      total_agent_hours: 1440,
      peak_day_agent_hours: 48,
      completed_turns: 300,
      active_days: 30,
    },
    leverage: null,
  };
}

function request(path, { method = "GET", cookie, csrf, body, ip = IP } = {}) {
  const headers = new Headers();
  if (cookie) headers.set("Cookie", cookie);
  if (csrf) headers.set("X-CSRF-Token", csrf);
  if (ip !== null) headers.set("CF-Connecting-IP", ip);
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    headers.set("Origin", ORIGIN);
  }
  return new Request(`${ORIGIN}/api/community${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function setCookies(response) {
  if (typeof response.headers.getSetCookie === "function") {
    return response.headers.getSetCookie();
  }
  const combined = response.headers.get("Set-Cookie") ?? "";
  return combined.match(/__Host-[^=]+=[^;,]*(?:;[^,]*)?/g) ?? [];
}

function cookiePair(setCookie, name) {
  const match = setCookie.match(new RegExp(`${name}=[^;]*`));
  assert.ok(match, `${name} cookie is present`);
  return match[0];
}

before(async () => {
  mf = new Miniflare({
    workers: [
      {
        config: {
          name: "community-security-auth-test",
          type: "worker",
          compatibilityDate: "2026-09-09",
          env: {
            COMMUNITY_DB: {
              type: "d1",
              id: `community-security-${crypto.randomUUID()}`,
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
    SITE_ORIGIN: ORIGIN,
    SUBMISSIONS_ENABLED: "true",
    GITHUB_CLIENT_ID: "synthetic-public-client",
    GITHUB_CLIENT_SECRET: "synthetic-oauth-secret-0123456789",
    SESSION_SECRET: "synthetic-session-secret-0123456789abcdef",
    TURNSTILE_SECRET_KEY: "synthetic-turnstile-secret-0123456789",
    TURNSTILE_SITE_KEY: "synthetic-site-key",
  };
});

after(async () => {
  await mf.dispose();
});

test("OAuth uses fixed no-scope PKCE, one-time state, hardened cookies, and session CSRF", async () => {
  const login = await routeCommunity(request("/auth/login"), env, { now: NOW });
  assert.equal(login.status, 302);
  assert.equal(login.headers.get("Cache-Control"), "no-store");
  assert.equal(login.headers.get("Referrer-Policy"), "no-referrer");

  const authorize = new URL(login.headers.get("Location"));
  assert.equal(authorize.origin, "https://github.com");
  assert.equal(authorize.pathname, "/login/oauth/authorize");
  assert.equal(authorize.searchParams.get("redirect_uri"), `${ORIGIN}/api/community/auth/callback`);
  assert.equal(authorize.searchParams.get("code_challenge_method"), "S256");
  assert.equal(authorize.searchParams.has("scope"), false);

  const oauthSet = setCookies(login).find((value) =>
    value.startsWith("__Host-community_oauth="),
  );
  assert.ok(oauthSet);
  assert.match(oauthSet, /; Path=\/; HttpOnly; Secure; SameSite=Lax;/);
  const oauthCookie = cookiePair(oauthSet, "__Host-community_oauth");
  const [cookieState, verifier] = decodeURIComponent(
    oauthCookie.slice(oauthCookie.indexOf("=") + 1),
  ).split(".");
  assert.equal(cookieState, authorize.searchParams.get("state"));
  assert.equal(await sha256(verifier), authorize.searchParams.get("code_challenge"));

  const callbackRequest = request(
    `/auth/callback?state=${encodeURIComponent(cookieState)}&code=synthetic-code`,
    { cookie: oauthCookie },
  );
  let fetchCalls = 0;
  const callback = await routeCommunity(callbackRequest, env, {
    now: NOW,
    fetchImpl: async (url, init) => {
      fetchCalls += 1;
      assert.equal(init.redirect, "error");
      assert.ok(init.signal instanceof AbortSignal);
      if (fetchCalls === 1) {
        assert.equal(url, "https://github.com/login/oauth/access_token");
        const tokenRequest = new URLSearchParams(init.body);
        assert.equal(tokenRequest.get("code"), "synthetic-code");
        assert.equal(tokenRequest.get("code_verifier"), verifier);
        assert.equal(tokenRequest.get("redirect_uri"), `${ORIGIN}/api/community/auth/callback`);
        return Response.json({ access_token: "synthetic-access-token" });
      }
      assert.equal(url, "https://api.github.com/user");
      assert.equal(init.headers.Authorization, "Bearer synthetic-access-token");
      return Response.json({ id: 4242, login: "security-review" });
    },
  });
  assert.equal(callback.status, 302);
  assert.equal(callback.headers.get("Location"), `${ORIGIN}/community/`);
  assert.equal(fetchCalls, 2);

  const callbackCookies = setCookies(callback);
  const sessionSet = callbackCookies.find((value) =>
    value.startsWith("__Host-community_session="),
  );
  assert.ok(sessionSet);
  assert.match(sessionSet, /; Path=\/; HttpOnly; Secure; SameSite=Lax;/);
  assert.match(sessionSet, /; Max-Age=604800$/);
  assert.ok(
    callbackCookies.some(
      (value) =>
        value.startsWith("__Host-community_oauth=") &&
        value.includes("Max-Age=0"),
    ),
  );
  const sessionCookie = cookiePair(sessionSet, "__Host-community_session");

  const session = await routeCommunity(
    request("/session", { cookie: sessionCookie }),
    env,
    { now: NOW },
  );
  const sessionBody = await session.json();
  assert.deepEqual(sessionBody.user, { login: "security-review" });
  assert.equal(sessionBody.authenticated, true);
  assert.match(sessionBody.csrf_token, /^[A-Za-z0-9_-]{43}$/);

  // Stopping new score submissions must not trap an existing login session.
  env.SUBMISSIONS_ENABLED = "false";
  const logout = await routeCommunity(
    request("/logout", {
      method: "POST",
      cookie: sessionCookie,
      csrf: sessionBody.csrf_token,
      body: {},
    }),
    env,
    { now: NOW },
  );
  assert.equal(logout.status, 200);
  assert.deepEqual(await logout.json(), { ok: true });

  let replayFetches = 0;
  const replay = await routeCommunity(
    request(
      `/auth/callback?state=${encodeURIComponent(cookieState)}&code=synthetic-code`,
      { cookie: oauthCookie },
    ),
    env,
    {
      now: NOW,
      fetchImpl: async () => {
        replayFetches += 1;
        throw new Error("one-time state must fail before a provider call");
      },
    },
  );
  assert.equal(replay.status, 400);
  assert.equal((await replay.json()).error, "oauth_state_invalid");
  assert.equal(replayFetches, 0);
});

test("the unauthenticated login rate bucket rejects before an eleventh state write", async () => {
  for (let index = 0; index < 10; index += 1) {
    const response = await routeCommunity(request("/auth/login"), env, {
      now: NOW,
    });
    assert.equal(response.status, 302);
  }
  const denied = await routeCommunity(request("/auth/login"), env, {
    now: NOW,
  });
  assert.equal(denied.status, 429);
  assert.equal((await denied.json()).error, "rate_limited");

  const states = await db
    .prepare("SELECT COUNT(*) AS count FROM oauth_states")
    .first();
  assert.equal(Number(states.count), 10);
  const bucket = await db
    .prepare("SELECT bucket_hash, attempts FROM rate_buckets")
    .first();
  assert.equal(bucket.attempts, 10);
  assert.notEqual(bucket.bucket_hash, IP);
  assert.equal(bucket.bucket_hash.includes(IP), false);
});

test("Turnstile receives the fixed action context, IP, and request idempotency key", async () => {
  const created = await createSession(db, {
    githubId: 777,
    login: "turnstile-review",
    now: NOW,
  });
  const requestId = "12345678-1234-4123-8123-123456789abc";
  let calls = 0;
  const response = await routeCommunity(
    request("/scores", {
      method: "POST",
      cookie: `__Host-community_session=${created.token}`,
      csrf: created.csrf,
      body: {
        score: validScore(),
        turnstile_token: "synthetic-turnstile-token",
        request_id: requestId,
      },
    }),
    env,
    {
      now: NOW,
      fetchImpl: async (url, init) => {
        calls += 1;
        assert.equal(
          url,
          "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        );
        assert.equal(init.redirect, "error");
        assert.ok(init.signal instanceof AbortSignal);
        const payload = JSON.parse(init.body);
        assert.deepEqual(payload, {
          secret: env.TURNSTILE_SECRET_KEY,
          response: "synthetic-turnstile-token",
          idempotency_key: requestId,
          remoteip: IP,
        });
        return Response.json({
          success: true,
          hostname: "agenthours.dev",
          action: "community_post",
        });
      },
    },
  );
  assert.equal(response.status, 200);
  assert.equal(calls, 1);
  assert.deepEqual(await response.json(), {
    ok: true,
    quota: { used: 1, remaining: 4, next_slot_at: null },
  });
});

test("concurrent replay of one accepted request stays idempotent", async () => {
  const created = await createSession(db, {
    githubId: 888,
    login: "replay-review",
    now: NOW,
  });
  const requestId = "abcdef12-3456-4789-8abc-def012345678";
  const body = {
    score: validScore(),
    turnstile_token: "one-logical-challenge",
    request_id: requestId,
  };
  let arrivals = 0;
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const fetchImpl = async (_url, init) => {
    arrivals += 1;
    assert.equal(JSON.parse(init.body).idempotency_key, requestId);
    if (arrivals === 2) release();
    await gate;
    return Response.json({
      success: true,
      hostname: "agenthours.dev",
      action: "community_post",
    });
  };
  const makeRequest = () =>
    request("/scores", {
      method: "POST",
      cookie: `__Host-community_session=${created.token}`,
      csrf: created.csrf,
      body,
    });
  const responses = await Promise.all([
    routeCommunity(makeRequest(), env, { now: NOW, fetchImpl }),
    routeCommunity(makeRequest(), env, { now: NOW, fetchImpl }),
  ]);
  assert.equal(arrivals, 2);
  assert.deepEqual(
    responses.map((response) => response.status),
    [200, 200],
  );
  const payloads = await Promise.all(responses.map((response) => response.json()));
  assert.ok(payloads.every((payload) => payload.ok && payload.quota.used === 1));
  const events = await db
    .prepare("SELECT COUNT(*) AS count FROM quota_events")
    .first();
  const scores = await db
    .prepare("SELECT COUNT(*) AS count FROM community_scores")
    .first();
  assert.equal(Number(events.count), 1);
  assert.equal(Number(scores.count), 1);
});
