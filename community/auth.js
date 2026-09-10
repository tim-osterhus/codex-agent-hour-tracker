import {
  clearCookie,
  getCookie,
  OAUTH_COOKIE,
  SESSION_COOKIE,
  serializeCookie,
} from "./cookies.js";
import { randomToken, sha256, timingSafeEqual } from "./crypto.js";

const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const OAUTH_TTL_MS = 10 * 60 * 1000;
const MAX_GITHUB_LOGIN = 39;

export class AuthError extends Error {
  constructor(code, status = 400) {
    super(code);
    this.name = "AuthError";
    this.code = code;
    this.status = status;
  }
}

function validSecret(value, minimum = 16) {
  return typeof value === "string" && value.length >= minimum && value.length <= 512;
}

export function getSiteOrigin(env) {
  if (typeof env?.SITE_ORIGIN !== "string" || env.SITE_ORIGIN.length > 256) return null;
  try {
    const url = new URL(env.SITE_ORIGIN);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      (url.pathname !== "/" && url.pathname !== "")
    ) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function getSubmissionConfig(env) {
  const origin = getSiteOrigin(env);
  const hasDatabase = env?.COMMUNITY_DB && typeof env.COMMUNITY_DB.prepare === "function";
  const configured = Boolean(
    origin &&
      hasDatabase &&
      typeof env.GITHUB_CLIENT_ID === "string" &&
      env.GITHUB_CLIENT_ID.length > 0 &&
      env.GITHUB_CLIENT_ID.length <= 128 &&
      validSecret(env.GITHUB_CLIENT_SECRET) &&
      validSecret(env.SESSION_SECRET, 32) &&
      validSecret(env.TURNSTILE_SECRET_KEY) &&
      typeof env.TURNSTILE_SITE_KEY === "string" &&
      env.TURNSTILE_SITE_KEY.length > 0 &&
      env.TURNSTILE_SITE_KEY.length <= 256,
  );
  return {
    configured,
    origin,
    hostname: origin ? new URL(origin).hostname : null,
    enabled: configured && env.SUBMISSIONS_ENABLED === "true",
    turnstile_site_key: configured && env.SUBMISSIONS_ENABLED === "true" ? env.TURNSTILE_SITE_KEY : null,
  };
}

function assertClock(now) {
  if (typeof now !== "number" || !Number.isFinite(now) || now < 0) throw new AuthError("server_unavailable", 503);
  return now;
}

function assertIdentity(githubId, login) {
  if (
    !Number.isSafeInteger(githubId) ||
    githubId <= 0 ||
    typeof login !== "string" ||
    login.length === 0 ||
    login.length > MAX_GITHUB_LOGIN ||
    !/^[A-Za-z0-9-]+$/.test(login)
  ) {
    throw new AuthError("oauth_failed", 502);
  }
}

export async function createSession(db, { githubId, login, now = Date.now() }) {
  assertIdentity(githubId, login);
  const nowMs = assertClock(now);
  const token = randomToken(32);
  // Derive the browser-readable CSRF token from the HttpOnly session token.
  // The raw CSRF value never needs to be persisted and remains stable across GETs.
  const csrf = await sha256(`csrf:${token}`);
  const createdAt = nowMs;
  const expiresAt = nowMs + SESSION_TTL_MS;
  const timestamp = new Date(nowMs).toISOString();
  await db.batch([
    db
      .prepare(
        "INSERT INTO github_users (github_id, login, created_at, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(github_id) DO UPDATE SET login = excluded.login, updated_at = excluded.updated_at",
      )
      .bind(githubId, login, timestamp, timestamp),
    db
      .prepare(
        "INSERT INTO sessions (token_hash, github_id, csrf_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
      )
      .bind(await sha256(token), githubId, await sha256(csrf), createdAt, expiresAt),
  ]);
  return { token, csrf, expiresAt };
}

export async function loadSession(request, db, { now = Date.now() } = {}) {
  const token = getCookie(request, SESSION_COOKIE);
  if (!token || token.length > 256) return null;
  const nowMs = assertClock(now);
  const row = await db
    .prepare(
      "SELECT s.token_hash, s.github_id, s.csrf_hash, u.login FROM sessions AS s JOIN github_users AS u ON u.github_id = s.github_id WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
    )
    .bind(await sha256(token), nowMs)
    .first();
  if (!row) return null;
  const csrfToken = await sha256(`csrf:${token}`);
  if (!(await timingSafeEqual(await sha256(csrfToken), row.csrf_hash))) return null;
  return {
    tokenHash: row.token_hash,
    githubId: Number(row.github_id),
    login: row.login,
    csrfHash: row.csrf_hash,
    csrfToken,
  };
}

export async function validateCsrf(request, session) {
  const token = request.headers.get("X-CSRF-Token") ?? "";
  if (token.length < 16 || token.length > 256) return false;
  return timingSafeEqual(token, session.csrfToken ?? "");
}

export function sessionCookie(token, expiresAt, now = Date.now()) {
  return serializeCookie(SESSION_COOKIE, token, {
    maxAge: Math.max(0, Math.floor((expiresAt - assertClock(now)) / 1000)),
  });
}

async function consumeState(db, request, now) {
  const encoded = getCookie(request, OAUTH_COOKIE);
  const state = new URL(request.url).searchParams.get("state") ?? "";
  const code = new URL(request.url).searchParams.get("code") ?? "";
  if (!encoded || encoded.length > 512 || !state || !code || state.length > 256 || code.length > 2048) {
    throw new AuthError("oauth_state_invalid", 400);
  }
  const separator = encoded.indexOf(".");
  if (separator <= 0 || separator === encoded.length - 1) throw new AuthError("oauth_state_invalid", 400);
  const cookieState = encoded.slice(0, separator);
  const verifier = encoded.slice(separator + 1);
  if (
    !/^[A-Za-z0-9_-]{43}$/.test(cookieState) ||
    !/^[A-Za-z0-9_-]{64}$/.test(verifier) ||
    !/^[A-Za-z0-9_-]{43}$/.test(state) ||
    !timingSafeEqual(cookieState, state)
  ) throw new AuthError("oauth_state_invalid", 400);
  const stateHash = await sha256(state);
  const verifierHash = await sha256(verifier);
  const row = await db
    .prepare("SELECT verifier_hash FROM oauth_states WHERE state_hash = ? AND expires_at > ? AND used_at IS NULL")
    .bind(stateHash, now)
    .first();
  if (!row || !timingSafeEqual(row.verifier_hash, verifierHash)) throw new AuthError("oauth_state_invalid", 400);
  const result = await db
    .prepare("UPDATE oauth_states SET used_at = ? WHERE state_hash = ? AND verifier_hash = ? AND used_at IS NULL AND expires_at > ?")
    .bind(now, stateHash, verifierHash, now)
    .run();
  if (Number(result?.meta?.changes ?? 0) !== 1) throw new AuthError("oauth_state_invalid", 400);
  return { code, verifier };
}

export async function beginGitHubLogin(env, { now = Date.now() } = {}) {
  const config = getSubmissionConfig(env);
  if (!config.configured) throw new AuthError("community_unavailable", 503);
  const nowMs = assertClock(now);
  const state = randomToken(32);
  const verifier = randomToken(48);
  const stateHash = await sha256(state);
  const verifierHash = await sha256(verifier);
  await env.COMMUNITY_DB.batch([
    env.COMMUNITY_DB
      .prepare("DELETE FROM oauth_states WHERE rowid IN (SELECT rowid FROM oauth_states WHERE expires_at <= ? LIMIT 25)")
      .bind(nowMs),
    env.COMMUNITY_DB
      .prepare("INSERT INTO oauth_states (state_hash, verifier_hash, created_at, expires_at) VALUES (?, ?, ?, ?)")
      .bind(stateHash, verifierHash, nowMs, nowMs + OAUTH_TTL_MS),
  ]);
  const callback = `${config.origin}/api/community/auth/callback`;
  const authorize = new URL("https://github.com/login/oauth/authorize");
  authorize.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
  authorize.searchParams.set("redirect_uri", callback);
  authorize.searchParams.set("state", state);
  authorize.searchParams.set("code_challenge", verifierHash);
  authorize.searchParams.set("code_challenge_method", "S256");
  const headers = new Headers({
    Location: authorize.toString(),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
  });
  headers.append("Set-Cookie", serializeCookie(OAUTH_COOKIE, `${state}.${verifier}`, { maxAge: Math.floor(OAUTH_TTL_MS / 1000) }));
  return new Response(null, { status: 302, headers });
}

async function boundedText(response, maxBytes) {
  const contentLength = Number(response.headers.get("Content-Length") ?? 0);
  if (contentLength > maxBytes) {
    if (response.body) await response.body.cancel().catch(() => {});
    throw new AuthError("oauth_failed", 502);
  }
  const reader = response.body?.getReader();
  if (!reader) {
    const text = await response.text();
    if (new TextEncoder().encode(text).byteLength > maxBytes) throw new AuthError("oauth_failed", 502);
    return text;
  }
  const chunks = [];
  let total = 0;
  let oversized = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        oversized = true;
        throw new AuthError("oauth_failed", 502);
      }
      chunks.push(value);
    }
  } finally {
    if (oversized) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

async function boundedJson(response, maxBytes) {
  if (!response.ok) throw new AuthError("oauth_failed", 502);
  let text;
  try {
    text = await boundedText(response, maxBytes);
    return JSON.parse(text);
  } catch (error) {
    if (error instanceof AuthError) throw error;
    throw new AuthError("oauth_failed", 502);
  }
}

export async function finishGitHubLogin(env, request, { now = Date.now(), fetchImpl = globalThis.fetch } = {}) {
  const config = getSubmissionConfig(env);
  if (!config.configured) throw new AuthError("community_unavailable", 503);
  const nowMs = assertClock(now);
  const { code, verifier } = await consumeState(env.COMMUNITY_DB, request, nowMs);
  const callback = `${config.origin}/api/community/auth/callback`;
  let tokenResponse;
  try {
    tokenResponse = await fetchImpl("https://github.com/login/oauth/access_token", {
      method: "POST",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
      headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        code,
        redirect_uri: callback,
        code_verifier: verifier,
      }),
    });
  } catch {
    throw new AuthError("oauth_failed", 502);
  }
  const tokenBody = await boundedJson(tokenResponse, 16_384);
  if (typeof tokenBody.access_token !== "string" || tokenBody.access_token.length === 0 || tokenBody.access_token.length > 2048) {
    throw new AuthError("oauth_failed", 502);
  }
  let identityResponse;
  try {
    identityResponse = await fetchImpl("https://api.github.com/user", {
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${tokenBody.access_token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-hours-community",
      },
    });
  } catch {
    throw new AuthError("oauth_failed", 502);
  }
  const identity = await boundedJson(identityResponse, 32_768);
  assertIdentity(identity?.id, identity?.login);
  const created = await createSession(env.COMMUNITY_DB, {
    githubId: identity.id,
    login: identity.login,
    now: nowMs,
  });
  const headers = new Headers({
    Location: `${config.origin}/community/`,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
  });
  headers.append("Set-Cookie", clearCookie(OAUTH_COOKIE));
  headers.append("Set-Cookie", sessionCookie(created.token, created.expiresAt, nowMs));
  return new Response(null, { status: 302, headers });
}

export async function revokeSession(db, session, now = Date.now()) {
  await db
    .prepare("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL")
    .bind(assertClock(now), session.tokenHash)
    .run();
}

export const SESSION_TTL = SESSION_TTL_MS;
