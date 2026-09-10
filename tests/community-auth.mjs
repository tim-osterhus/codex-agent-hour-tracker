import test, { after, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { Miniflare } from "miniflare";
import { readFile } from "node:fs/promises";
import { OAUTH_COOKIE, SESSION_COOKIE } from "../community/cookies.js";
import { getSiteOrigin } from "../community/auth.js";
import { routeCommunity } from "../community/api.js";

const NOW = Date.parse("2026-09-09T12:00:00.000Z");
const schema = (await readFile(new URL("../migrations/0001_community.sql", import.meta.url), "utf8")).replaceAll("\n", " ");
let mf;
let db;
let env;

function request(path, { method = "GET", cookie, origin, body } = {}) {
  const headers = new Headers();
  if (cookie) headers.set("Cookie", cookie);
  if (origin) headers.set("Origin", origin);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  return new Request(`https://agenthours.dev/api/community${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function cookieValue(response, name) {
  const values = typeof response.headers.getSetCookie === "function"
    ? response.headers.getSetCookie()
    : [response.headers.get("set-cookie") ?? ""];
  const line = values.find((value) => value.startsWith(`${name}=`));
  assert.ok(line, `missing ${name} cookie`);
  return line.slice(name.length + 1).split(";", 1)[0];
}

function envForTests() {
  return {
    COMMUNITY_DB: db,
    SITE_ORIGIN: "https://agenthours.dev",
    SUBMISSIONS_ENABLED: "false",
    GITHUB_CLIENT_ID: "public-client",
    GITHUB_CLIENT_SECRET: "oauth-secret-0123456789abcdef",
    SESSION_SECRET: "session-secret-0123456789abcdef0",
    TURNSTILE_SECRET_KEY: "turnstile-secret-0123456789abcdef",
    TURNSTILE_SITE_KEY: "site-key",
  };
}

before(async () => {
  mf = new Miniflare({
    workers: [{
      config: {
        name: "community-auth-test",
        type: "worker",
        compatibilityDate: "2026-09-09",
        env: { COMMUNITY_DB: { type: "d1", id: `community-auth-${crypto.randomUUID()}` } },
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
  env = envForTests();
  await db.batch([
    db.prepare("DELETE FROM sessions"),
    db.prepare("DELETE FROM oauth_states"),
    db.prepare("DELETE FROM rate_buckets"),
    db.prepare("DELETE FROM community_scores"),
    db.prepare("DELETE FROM quota_events"),
    db.prepare("DELETE FROM github_users"),
  ]);
});

after(async () => {
  await mf.dispose();
});

test("SITE_ORIGIN accepts only an origin-shaped HTTPS URL", () => {
  assert.equal(getSiteOrigin({ SITE_ORIGIN: "https://agenthours.dev/" }), "https://agenthours.dev");
  for (const SITE_ORIGIN of [
    "http://agenthours.dev",
    "https://agenthours.dev/community",
    "https://agenthours.dev/?next=evil",
    "https://user:pass@agenthours.dev",
    "https://agenthours.dev/#fragment",
  ]) {
    assert.equal(getSiteOrigin({ SITE_ORIGIN }), null, SITE_ORIGIN);
  }
});

test("login uses a short-lived state cookie, fixed callback, and S256 PKCE without scopes", async () => {
  const response = await routeCommunity(request("/auth/login"), env, { now: NOW });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  const location = new URL(response.headers.get("location"));
  assert.equal(location.origin, "https://github.com");
  assert.equal(location.pathname, "/login/oauth/authorize");
  assert.equal(location.searchParams.get("client_id"), "public-client");
  assert.equal(location.searchParams.get("redirect_uri"), "https://agenthours.dev/api/community/auth/callback");
  assert.equal(location.searchParams.get("scope"), null);
  assert.equal(location.searchParams.get("code_challenge_method"), "S256");
  assert.match(location.searchParams.get("code_challenge"), /^[A-Za-z0-9_-]{43}$/);
  assert.match(location.searchParams.get("state"), /^[A-Za-z0-9_-]{43}$/);
  const oauthCookie = cookieValue(response, OAUTH_COOKIE);
  assert.match(decodeURIComponent(oauthCookie), /^[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{64}$/);
  assert.match(response.headers.get("set-cookie"), /HttpOnly/);
  assert.match(response.headers.get("set-cookie"), /Secure/);
  assert.match(response.headers.get("set-cookie"), /SameSite=Lax/);
  assert.match(response.headers.get("set-cookie"), /Max-Age=600/);
  assert.equal((await db.prepare("SELECT COUNT(*) AS count FROM oauth_states").first()).count, 1);
});

test("callback consumes state once, sends bounded external requests, and exposes only the login", async () => {
  const login = await routeCommunity(request("/auth/login"), env, { now: NOW });
  const location = new URL(login.headers.get("location"));
  const oauthCookie = cookieValue(login, OAUTH_COOKIE);
  const callback = request(`/auth/callback?state=${encodeURIComponent(location.searchParams.get("state"))}&code=oauth-code`, {
    cookie: `${OAUTH_COOKIE}=${oauthCookie}`,
  });
  const calls = [];
  const response = await routeCommunity(callback, env, {
    now: NOW + 1_000,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      assert.equal(options.redirect, "manual");
      assert.ok(options.signal instanceof AbortSignal);
      if (url.includes("access_token")) {
        assert.equal(options.method, "POST");
        const body = new URLSearchParams(options.body);
        assert.equal(body.get("redirect_uri"), "https://agenthours.dev/api/community/auth/callback");
        assert.equal(body.get("client_secret"), env.GITHUB_CLIENT_SECRET);
        assert.equal(body.get("code_verifier"), decodeURIComponent(oauthCookie).split(".")[1]);
        return new Response(JSON.stringify({ access_token: "github-access-token" }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      assert.equal(url, "https://api.github.com/user");
      assert.equal(options.headers.Authorization, "Bearer github-access-token");
      return new Response(JSON.stringify({ id: 9001, login: "octocat" }), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("location"), "https://agenthours.dev/community/");
  assert.equal(calls.length, 2);
  const sessionCookie = cookieValue(response, SESSION_COOKIE);
  const sessionResponse = await routeCommunity(request("/session", { cookie: `${SESSION_COOKIE}=${sessionCookie}` }), env, {
    now: NOW + 1_000,
  });
  const sessionBody = await sessionResponse.json();
  assert.equal(sessionBody.authenticated, true);
  assert.deepEqual(sessionBody.user, { login: "octocat" });
  assert.match(sessionBody.csrf_token, /^[A-Za-z0-9_-]{43}$/);
  assert.deepEqual(sessionBody.quota, { used: 0, remaining: 5, next_slot_at: null });
  assert.deepEqual(sessionBody.scores, []);
  const replay = await routeCommunity(callback, env, {
    now: NOW + 2_000,
    fetchImpl: async () => {
      throw new Error("replay must not call GitHub");
    },
  });
  assert.equal(replay.status, 400);
  assert.equal((await replay.json()).error, "oauth_state_invalid");
  assert.equal((await db.prepare("SELECT COUNT(*) AS count FROM sessions").first()).count, 1);
});

test("configured sign-in remains available while new submissions are disabled", async () => {
  const response = await routeCommunity(request("/auth/login"), env, { now: NOW });
  assert.equal(response.status, 302);
});
