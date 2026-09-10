import {
  AuthError,
  beginGitHubLogin,
  finishGitHubLogin,
  getSiteOrigin,
  getSubmissionConfig,
  loadSession,
  revokeSession,
  validateCsrf,
} from "./auth.js";
import { clearCookie, SESSION_COOKIE } from "./cookies.js";
import { hmacSha256 } from "./crypto.js";
import {
  consumeRateBucket,
  deleteOwnScore,
  findAcceptedRequest,
  getPublicScores,
  getUserScores,
  pruneRateBuckets,
  pruneSessions,
  quotaForUser,
  recordAcceptedScore,
} from "./db.js";
import { parseScore, scoreDigest, ScoreValidationError } from "./score.js";

const MAX_BODY_BYTES = 64 * 1024;
const MAX_TURNSTILE_RESPONSE_BYTES = 16 * 1024;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

class ApiError extends Error {
  constructor(code, status, message) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

const dbRequired = (env) => {
  if (!env?.COMMUNITY_DB || typeof env.COMMUNITY_DB.prepare !== "function") {
    throw new ApiError("community_unavailable", 503, "Community service is unavailable.");
  }
  return env.COMMUNITY_DB;
};

function responseJson(value, status = 200, { cache = "no-store", cookies = [] } = {}) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Cache-Control": cache,
  });
  for (const cookie of cookies) headers.append("Set-Cookie", cookie);
  return new Response(JSON.stringify(value), { status, headers });
}

function redirectResponse(location, cookies = []) {
  const headers = new Headers({ Location: location, "Cache-Control": "no-store" });
  for (const cookie of cookies) headers.append("Set-Cookie", cookie);
  return new Response(null, { status: 302, headers });
}

function error(code, status, message) {
  return new ApiError(code, status, message);
}

function enabledOrThrow(env) {
  const config = getSubmissionConfig(env);
  if (!config.enabled) throw error("submissions_disabled", 503, "Community submissions are disabled.");
  return config;
}

function configuredOrThrow(env) {
  const config = getSubmissionConfig(env);
  if (!config.configured) {
    throw error("community_unavailable", 503, "Community service is unavailable.");
  }
  return config;
}

function assertOrigin(request, env) {
  const origin = getSiteOrigin(env);
  if (!origin || request.headers.get("Origin") !== origin) {
    throw error("origin_invalid", 403, "The request origin is not allowed.");
  }
}

async function requireSession(request, env, now) {
  configuredOrThrow(env);
  const session = await loadSession(request, dbRequired(env), { now });
  if (!session) throw error("authentication_required", 401, "GitHub sign-in is required.");
  return session;
}

async function requireMutationSession(request, env, now) {
  assertOrigin(request, env);
  const session = await requireSession(request, env, now);
  if (!(await validateCsrf(request, session))) {
    throw error("csrf_invalid", 403, "The CSRF token is invalid.");
  }
  return session;
}

async function boundedRequestText(request, maxBytes = MAX_BODY_BYTES) {
  const contentLength = Number(request.headers.get("Content-Length") ?? 0);
  if (Number.isFinite(contentLength) && contentLength > maxBytes) {
    throw error("request_too_large", 413, "The request body is too large.");
  }
  const reader = request.body?.getReader();
  if (!reader) {
    const text = await request.text();
    if (new TextEncoder().encode(text).byteLength > maxBytes) {
      throw error("request_too_large", 413, "The request body is too large.");
    }
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
        throw error("request_too_large", 413, "The request body is too large.");
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
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw error("request_invalid", 400, "The request body is invalid JSON.");
  }
}

async function readJsonBody(request, expectedKeys) {
  const contentType = request.headers.get("Content-Type") ?? "";
  if (!/^application\/json(?:\s*;|$)/i.test(contentType)) {
    throw error("request_invalid", 400, "The request must use JSON.");
  }
  let body;
  try {
    body = JSON.parse(await boundedRequestText(request));
  } catch (caught) {
    if (caught instanceof ApiError) throw caught;
    throw error("request_invalid", 400, "The request body is invalid JSON.");
  }
  if (
    body === null ||
    typeof body !== "object" ||
    Array.isArray(body) ||
    Object.keys(body).length !== expectedKeys.length ||
    expectedKeys.some((key) => !Object.hasOwn(body, key))
  ) {
    throw error("request_invalid", 400, "The request fields are invalid.");
  }
  return body;
}

async function boundedResponseText(response, maxBytes) {
  const contentLength = Number(response.headers.get("Content-Length") ?? 0);
  if (Number.isFinite(contentLength) && contentLength > maxBytes) {
    if (response.body) await response.body.cancel().catch(() => {});
    return null;
  }
  const reader = response.body?.getReader();
  if (!reader) {
    const text = await response.text();
    return new TextEncoder().encode(text).byteLength <= maxBytes ? text : null;
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
        return null;
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
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

async function verifyTurnstile(env, request, token, requestId, fetchImpl) {
  const ip = request.headers.get("CF-Connecting-IP");
  const payload = {
    secret: env.TURNSTILE_SECRET_KEY,
    response: token,
    idempotency_key: requestId,
  };
  if (ip && ip.length <= 64) payload.remoteip = ip;
  let result;
  try {
    const response = await fetchImpl("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const text = await boundedResponseText(response, MAX_TURNSTILE_RESPONSE_BYTES);
    if (!response.ok || text === null) return false;
    result = JSON.parse(text);
  } catch {
    return false;
  }
  const hostname = getSubmissionConfig(env).hostname;
  return Boolean(
    result &&
      result.success === true &&
      result.hostname === hostname &&
      result.action === "community_post",
  );
}

async function consumeWriteRateLimit(db, env, request, now) {
  const suppliedIp = request.headers.get("CF-Connecting-IP");
  const ip = suppliedIp && suppliedIp.length <= 64 ? suppliedIp : "unknown";
  const bucketStart = Math.floor(now / 60_000) * 60_000;
  const bucketHash = await hmacSha256(env.SESSION_SECRET, `${ip}\u0000${bucketStart}`);
  await pruneRateBuckets(db, now);
  return consumeRateBucket(db, bucketHash, bucketStart);
}

function requestIdValue(value) {
  if (typeof value !== "string" || !UUID_V4.test(value)) {
    throw error("request_invalid", 400, "The request ID must be a UUID.");
  }
  return value.toLowerCase();
}

async function postScore(request, env, { now, fetchImpl }) {
  enabledOrThrow(env);
  const session = await requireMutationSession(request, env, now);
  const db = dbRequired(env);
  const body = await readJsonBody(request, ["score", "turnstile_token", "request_id"]);
  const requestId = requestIdValue(body.request_id);
  if (typeof body.turnstile_token !== "string" || body.turnstile_token.length === 0 || body.turnstile_token.length > 2048) {
    throw error("request_invalid", 400, "The Turnstile token is invalid.");
  }
  let score;
  try {
    score = parseScore(body.score, { now });
  } catch (caught) {
    if (caught instanceof ScoreValidationError) throw error("score_invalid", 400, "The public score is invalid.");
    throw caught;
  }
  const digest = await scoreDigest(score);
  const existing = await findAcceptedRequest(db, requestId);
  if (existing) {
    if (
      Number(existing.github_id) !== session.githubId ||
      existing.scope !== score.scope ||
      existing.score_digest !== digest
    ) {
      throw error("request_id_conflict", 409, "The request ID was already used for another score.");
    }
    return responseJson({
      ok: true,
      quota: await quotaForUser(db, session.githubId, now),
    });
  }
  if (!(await consumeWriteRateLimit(db, env, request, now))) {
    throw error("rate_limited", 429, "Too many community submissions. Try again shortly.");
  }
  if (!(await verifyTurnstile(env, request, body.turnstile_token, requestId, fetchImpl))) {
    throw error("turnstile_invalid", 400, "Turnstile verification failed.");
  }
  const result = await recordAcceptedScore(db, {
    githubId: session.githubId,
    login: session.login,
    requestId,
    score,
    now,
  });
  if (result.status === "request_conflict") throw error("request_id_conflict", 409, "The request ID was already used for another score.");
  if (result.status === "quota_exceeded") throw error("quota_exceeded", 429, "The rolling community posting quota has been reached.");
  return responseJson({ ok: true, quota: result.quota });
}

async function deleteScore(request, env, { now }) {
  const session = await requireMutationSession(request, env, now);
  const body = await readJsonBody(request, ["scope"]);
  if (body.scope !== "interactive-only" && body.scope !== "including-exec") {
    throw error("request_invalid", 400, "The score scope is invalid.");
  }
  if (!(await consumeWriteRateLimit(dbRequired(env), env, request, now))) {
    throw error("rate_limited", 429, "Too many community requests. Try again shortly.");
  }
  await deleteOwnScore(dbRequired(env), { githubId: session.githubId, scope: body.scope });
  return responseJson({ ok: true });
}

async function logout(request, env, { now }) {
  const session = await requireMutationSession(request, env, now);
  const db = dbRequired(env);
  if (!(await consumeWriteRateLimit(db, env, request, now))) {
    throw error("rate_limited", 429, "Too many community requests. Try again shortly.");
  }
  await revokeSession(db, session, now);
  return responseJson({ ok: true }, 200, { cookies: [clearCookie(SESSION_COOKIE)] });
}

async function session(request, env, now) {
  const config = getSubmissionConfig(env);
  if (!config.configured) {
    return responseJson({ authenticated: false, user: null, csrf_token: null, quota: null, scores: [] });
  }
  const db = dbRequired(env);
  const current = await loadSession(request, db, { now });
  if (!current) {
    return responseJson({ authenticated: false, user: null, csrf_token: null, quota: null, scores: [] });
  }
  return responseJson({
    authenticated: true,
    user: { login: current.login },
    csrf_token: current.csrfToken,
    quota: await quotaForUser(db, current.githubId, now),
    scores: await getUserScores(db, current.githubId),
  });
}

async function board(url, env, now) {
  const db = dbRequired(env);
  if (url.searchParams.getAll("scope").length !== 1 || [...url.searchParams.keys()].some((key) => key !== "scope")) {
    throw error("request_invalid", 400, "A score scope is required.");
  }
  const scope = url.searchParams.get("scope");
  if (scope !== "interactive-only" && scope !== "including-exec") {
    throw error("request_invalid", 400, "The score scope is invalid.");
  }
  return responseJson({ scores: await getPublicScores(db, scope, { now, limit: 50 }) }, 200, { cache: "public, max-age=60" });
}

const AUTH_MESSAGES = Object.freeze({
  community_unavailable: "Community service is unavailable.",
  oauth_state_invalid: "The sign-in state is invalid or expired.",
  oauth_failed: "GitHub sign-in could not be completed.",
  submissions_disabled: "Community submissions are disabled.",
  server_unavailable: "Community service is unavailable.",
});

function mapError(caught) {
  if (caught instanceof ApiError) return responseJson({ error: caught.code, message: caught.message }, caught.status);
  if (caught instanceof AuthError) {
    return responseJson(
      { error: caught.code, message: AUTH_MESSAGES[caught.code] ?? "GitHub sign-in could not be completed." },
      caught.status,
    );
  }
  if (caught instanceof ScoreValidationError) return responseJson({ error: "score_invalid", message: "The public score is invalid." }, 400);
  return responseJson({ error: "server_error", message: "Community service is unavailable." }, 500);
}

/** Main route used by the Pages catch-all Function and direct integration tests. */
export async function routeCommunity(request, env, { now = Date.now(), fetchImpl = globalThis.fetch } = {}) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/community\/?/, "");
  try {
    if (request.method === "GET" && path === "config") {
      const config = getSubmissionConfig(env);
      return responseJson(
        { enabled: config.enabled, turnstile_site_key: config.turnstile_site_key },
        200,
        { cache: "public, max-age=60" },
      );
    }
    if (request.method === "GET" && path === "session") return await session(request, env, now);
    if (request.method === "GET" && path === "scores") return await board(url, env, now);
    if (request.method === "GET" && path === "auth/login") {
      configuredOrThrow(env);
      const db = dbRequired(env);
      if (!(await consumeWriteRateLimit(db, env, request, now))) {
        throw error("rate_limited", 429, "Too many community requests. Try again shortly.");
      }
      await pruneSessions(db, now);
      return await beginGitHubLogin(env, { now });
    }
    if (request.method === "GET" && path === "auth/callback") {
      configuredOrThrow(env);
      const db = dbRequired(env);
      if (!(await consumeWriteRateLimit(db, env, request, now))) {
        throw error("rate_limited", 429, "Too many community requests. Try again shortly.");
      }
      await pruneSessions(db, now);
      return await finishGitHubLogin(env, request, { now, fetchImpl });
    }
    if (request.method === "POST" && path === "scores") return await postScore(request, env, { now, fetchImpl });
    if (request.method === "DELETE" && path === "scores") return await deleteScore(request, env, { now });
    if (request.method === "POST" && path === "logout") return await logout(request, env, { now });
    throw error("not_found", 404, "Community route not found.");
  } catch (caught) {
    if (caught instanceof Error && caught.code && caught.status) return mapError(caught);
    return mapError(caught);
  }
}

export { ApiError, MAX_BODY_BYTES };
