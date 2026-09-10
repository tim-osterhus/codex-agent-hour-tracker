import test, { before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Miniflare } from "miniflare";

const ORIGIN = "https://agenthours.dev";
let mf, db, redirectPath;
let outbound = [];

before(async () => {
  const modules = {};
  for (const name of ["api", "auth", "cookies", "crypto", "db", "score"]) {
    modules[`community/${name}.js`] = {
      type: "esm",
      contents: await readFile(new URL(`../community/${name}.js`, import.meta.url), "utf8"),
    };
  }
  modules["index.js"] = { type: "esm", contents: `
    import { routeCommunity } from './community/api.js';
    export default { fetch(request, env) {
      return routeCommunity(request, { ...env,
        SITE_ORIGIN: '${ORIGIN}', SUBMISSIONS_ENABLED: 'true',
        GITHUB_CLIENT_ID: 'synthetic-client', GITHUB_CLIENT_SECRET: 'synthetic-github-secret',
        SESSION_SECRET: 'synthetic-session-secret-at-least-32-bytes',
        TURNSTILE_SECRET_KEY: 'synthetic-turnstile-secret', TURNSTILE_SITE_KEY: 'synthetic-key'
      }, { now: Date.parse('2026-09-09T12:00:00Z') });
    } };
  ` };
  mf = new Miniflare({
    workers: [{
      config: {
        name: "community-runtime-test", type: "worker", compatibilityDate: "2026-09-09",
        env: { COMMUNITY_DB: { type: "d1", id: "community-runtime-test" } },
        manifest: { mainModule: "index.js", modules },
      },
      dev: { outboundService: { type: "fetcher", handler: async (request) => {
        // Intercept only the external provider; production fetch still runs in workerd.
        const url = new URL(request.url);
        outbound.push(url.href);
        if (url.pathname === redirectPath) {
          return new Response(null, { status: 307, headers: { Location: "https://unexpected.example/leak" } });
        }
        if (url.href === "https://github.com/login/oauth/access_token") {
          return Response.json({ access_token: "synthetic-token", token_type: "bearer", scope: "" });
        }
        if (url.href === "https://api.github.com/user") {
          return Response.json({ id: 42, login: "synthetic-user" });
        }
        if (url.href === "https://challenges.cloudflare.com/turnstile/v0/siteverify") {
          return Response.json({ success: true, hostname: "agenthours.dev", action: "community_post" });
        }
        return new Response("Unexpected outbound request", { status: 500 });
      } } },
    }],
    logRequests: false, telemetry: { enabled: false },
  });
  db = await mf.getD1Database("COMMUNITY_DB");
  await db.exec((await readFile(new URL("../migrations/0001_community.sql", import.meta.url), "utf8")).replaceAll("\n", " "));
});

after(async () => { await mf?.dispose(); });
beforeEach(async () => {
  redirectPath = null;
  outbound = [];
  await db.batch(["sessions", "oauth_states", "community_scores", "quota_events", "rate_buckets", "github_users"].map(table => db.prepare(`DELETE FROM ${table}`)));
});

const call = (path, options = {}) => mf.dispatchFetch(`${ORIGIN}/api/community${path}`, { redirect: "manual", ...options });
function cookie(response, name) {
  const value = response.headers.getSetCookie().find(value => value.startsWith(`${name}=`));
  assert.ok(value, `missing ${name}`);
  return value.split(";", 1)[0];
}
async function login() {
  const start = await call("/auth/login");
  assert.equal(start.status, 302);
  const state = new URL(start.headers.get("location")).searchParams.get("state");
  return call(`/auth/callback?code=synthetic-code&state=${state}`, {
    headers: { Cookie: cookie(start, "__Host-community_oauth") },
  });
}

test("Workers completes OAuth, preserves the session, verifies Turnstile, and revokes logout", async () => {
  const result = await login();
  assert.equal(result.status, 302);
  const sessionCookie = cookie(result, "__Host-community_session");
  const headers = { Cookie: sessionCookie, Origin: ORIGIN, "Content-Type": "application/json" };
  const session = await (await call("/session", { headers })).json();
  assert.equal(session.authenticated, true);
  assert.deepEqual(session.user, { login: "synthetic-user" });
  assert.equal((await (await call("/session", { headers })).json()).authenticated, true);
  headers["X-CSRF-Token"] = session.csrf_token;
  const body = JSON.stringify({ request_id: "12345678-1234-4123-8123-123456789abc", turnstile_token: "synthetic-challenge", score: {
    schema: "agent-hours-score", schema_version: 1, tracker_version: "0.2.0", methodology_version: "1",
    window: { start: "2026-08-10", end: "2026-09-08", days: 30, timezone: "UTC" }, scope: "interactive-only",
    metrics: { agent_hours_per_day: 1, total_agent_hours: 30, peak_day_agent_hours: 1, completed_turns: 30, active_days: 30 }, leverage: null,
  } });
  redirectPath = "/turnstile/v0/siteverify";
  const refused = await call("/scores", { method: "POST", headers, body });
  assert.equal(refused.status, 400);
  assert.equal((await refused.json()).error, "turnstile_invalid");
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM quota_events").first()).n, 0);
  redirectPath = null;
  assert.equal((await call("/scores", { method: "POST", headers, body })).status, 200);
  assert.equal((await call("/logout", { method: "POST", headers })).status, 200);
  assert.equal((await (await call("/session", { headers })).json()).authenticated, false);
  assert.ok(outbound.includes("https://challenges.cloudflare.com/turnstile/v0/siteverify"));
  assert.ok(!outbound.some(url => url.includes("unexpected.example")));
});

for (const path of ["/login/oauth/access_token", "/user"]) {
  test(`Workers rejects upstream redirects at ${path} without forwarding credentials`, async () => {
    redirectPath = path;
    const response = await login();
    assert.equal(response.status, 502);
    assert.equal((await response.json()).error, "oauth_failed");
    assert.ok(outbound.some(url => new URL(url).pathname === path));
    assert.ok(!outbound.some(url => url.includes("unexpected.example")));
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM sessions").first()).n, 0);
  });
}
