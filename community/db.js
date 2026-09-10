import { canonicalJson, scoreDigest, scoreExpiryAt } from "./score.js";

const WINDOW_MS = 30 * 24 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_BOARD_ROWS = 50;

export class DatabaseError extends Error {
  constructor(message = "Community database unavailable.") {
    super(message);
    this.name = "DatabaseError";
  }
}

const assertNow = (now) => {
  if (typeof now !== "number" || !Number.isFinite(now) || now < 0) {
    throw new DatabaseError();
  }
  return now;
};

const changes = (result) => Number(result?.meta?.changes ?? 0);

function normalizedScope(scope) {
  if (scope !== "interactive-only" && scope !== "including-exec") {
    throw new DatabaseError();
  }
  return scope;
}

async function requestRow(db, requestId) {
  return db
    .prepare(
      "SELECT request_id, github_id, scope, score_digest FROM quota_events WHERE request_id = ?",
    )
    .bind(requestId)
    .first();
}

export async function findAcceptedRequest(db, requestId) {
  if (typeof requestId !== "string" || requestId.length > 128) return null;
  return requestRow(db, requestId);
}

function requestResult(existing, { githubId, scope, digest }) {
  if (!existing) return null;
  if (
    Number(existing.github_id) !== githubId ||
    existing.scope !== scope ||
    existing.score_digest !== digest
  ) {
    return { status: "request_conflict" };
  }
  return { status: "replayed" };
}

async function quotaSummary(db, githubId, now) {
  const lowerBound = assertNow(now) - WINDOW_MS;
  const row = await db
    .prepare(
      "SELECT COUNT(*) AS used, MIN(accepted_at) AS earliest FROM quota_events WHERE github_id = ? AND accepted_at > ?",
    )
    .bind(githubId, lowerBound)
    .first();
  const used = Math.min(5, Math.max(0, Number(row?.used ?? 0)));
  const earliest = row?.earliest === null || row?.earliest === undefined
    ? null
    : Number(row.earliest);
  return {
    used,
    remaining: Math.max(0, 5 - used),
    next_slot_at:
      used >= 4 && Number.isFinite(earliest)
        ? new Date(earliest + WINDOW_MS).toISOString()
        : null,
  };
}

export async function quotaForUser(db, githubId, now) {
  return quotaSummary(db, githubId, now);
}

/** Remove a bounded number of unusable session rows during normal traffic. */
export async function pruneSessions(db, now) {
  const cutoff = assertNow(now);
  await db
    .prepare(
      "DELETE FROM sessions WHERE rowid IN (SELECT rowid FROM sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL LIMIT 25)",
    )
    .bind(cutoff)
    .run();
}

/**
 * Insert one accepted event and replace the user's current score atomically.
 * The database trigger, not this JavaScript count, owns the five-event cap.
 */
export async function recordAcceptedScore(
  db,
  { githubId, login, requestId, score, now },
) {
  if (!Number.isSafeInteger(githubId) || githubId <= 0 || typeof login !== "string") {
    throw new DatabaseError();
  }
  if (typeof requestId !== "string" || !requestId) throw new DatabaseError();
  const scope = normalizedScope(score?.scope);
  const nowMs = assertNow(now);
  const digest = await scoreDigest(score);
  const existing = await requestRow(db, requestId);
  const prior = requestResult(existing, { githubId, scope, digest });
  if (prior) {
    return { ...prior, quota: await quotaSummary(db, githubId, nowMs) };
  }

  const scoreJson = canonicalJson(score);
  const updatedAt = new Date(nowMs).toISOString();
  const cleanupCutoff = nowMs - WINDOW_MS;
  try {
    const results = await db.batch([
      // Keep the private rolling ledger bounded. The subquery limits each request's cleanup.
      db
        .prepare(
          "DELETE FROM quota_events WHERE rowid IN (SELECT rowid FROM quota_events WHERE accepted_at <= ? LIMIT 25)",
        )
        .bind(cleanupCutoff),
      db
        .prepare(
          "INSERT INTO github_users (github_id, login, created_at, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(github_id) DO UPDATE SET login = excluded.login, updated_at = excluded.updated_at",
        )
        .bind(githubId, login, updatedAt, updatedAt),
      db
        .prepare(
          "INSERT INTO quota_events (request_id, github_id, scope, score_digest, accepted_at) VALUES (?, ?, ?, ?, ?)",
        )
        .bind(requestId, githubId, scope, digest, nowMs),
      db
        .prepare(
          "INSERT INTO community_scores (github_id, scope, login, score_json, score_digest, window_end, window_timezone, eligible_until, agent_hours_per_day, updated_at, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(github_id, scope) DO UPDATE SET login = excluded.login, score_json = excluded.score_json, score_digest = excluded.score_digest, window_end = excluded.window_end, window_timezone = excluded.window_timezone, eligible_until = excluded.eligible_until, agent_hours_per_day = excluded.agent_hours_per_day, updated_at = excluded.updated_at, request_id = excluded.request_id",
        )
        .bind(
          githubId,
          scope,
          login,
          scoreJson,
          digest,
          score.window.end,
          score.window.timezone,
          scoreExpiryAt(score),
          score.metrics.agent_hours_per_day,
          updatedAt,
          requestId,
        ),
    ]);
    if (!Array.isArray(results) || !results[2]) throw new DatabaseError();
  } catch (error) {
    // A duplicate in a concurrent retry can lose the race before the first lookup.
    const after = await requestRow(db, requestId);
    const raced = requestResult(after, { githubId, scope, digest });
    if (raced) return { ...raced, quota: await quotaSummary(db, githubId, nowMs) };
    const quota = await quotaSummary(db, githubId, nowMs);
    if (quota.used >= 5) return { status: "quota_exceeded", quota };
    if (error instanceof DatabaseError) throw error;
    throw new DatabaseError();
  }
  return {
    status: "accepted",
    quota: await quotaSummary(db, githubId, nowMs),
  };
}

export async function deleteOwnScore(db, { githubId, scope }) {
  normalizedScope(scope);
  await db
    .prepare("DELETE FROM community_scores WHERE github_id = ? AND scope = ?")
    .bind(githubId, scope)
    .run();
}

export async function getUserScores(db, githubId) {
  const rows = await db
    .prepare(
      "SELECT login, score_json, updated_at FROM community_scores WHERE github_id = ? ORDER BY scope ASC",
    )
    .bind(githubId)
    .all();
  return (rows.results ?? []).flatMap((row) => {
    try {
      return [{ login: row.login, score: JSON.parse(row.score_json), updated_at: row.updated_at }];
    } catch {
      return [];
    }
  });
}

export async function getPublicScores(db, scope, { now, limit = MAX_BOARD_ROWS } = {}) {
  normalizedScope(scope);
  const nowMs = assertNow(now);
  const boundedLimit = Math.min(MAX_BOARD_ROWS, Math.max(1, Number(limit) || MAX_BOARD_ROWS));
  const rows = await db
    .prepare(
      "SELECT login, score_json, updated_at FROM community_scores WHERE scope = ? AND eligible_until > ? ORDER BY agent_hours_per_day DESC, login ASC LIMIT ?",
    )
    .bind(scope, nowMs, boundedLimit)
    .all();
  return (rows.results ?? []).flatMap((row) => {
    try {
      return [{ login: row.login, score: JSON.parse(row.score_json), updated_at: row.updated_at }];
    } catch {
      return [];
    }
  });
}

export async function pruneRateBuckets(db, now) {
  const cutoff = assertNow(now) - 2 * 60 * 1000;
  await db
    .prepare(
      "DELETE FROM rate_buckets WHERE rowid IN (SELECT rowid FROM rate_buckets WHERE bucket_start < ? LIMIT 25)",
    )
    .bind(cutoff)
    .run();
}

export async function consumeRateBucket(db, bucketHash, bucketStart) {
  const result = await db
    .prepare(
      "INSERT INTO rate_buckets (bucket_hash, bucket_start, attempts) VALUES (?, ?, 1) ON CONFLICT(bucket_hash) DO UPDATE SET attempts = attempts + 1 WHERE attempts < 10",
    )
    .bind(bucketHash, bucketStart)
    .run();
  return changes(result) === 1;
}

export { WINDOW_MS };
