const DAY_MS = 86_400_000;
const MAX_SCORE_BYTES = 32_768;
const MAX_HOURS_PER_DAY = 1_000_000_000;
const MAX_TOTAL_HOURS = 30_000_000_000;
const MAX_TURNS = 100_000_000;
const SCOPES = new Set(["interactive-only", "including-exec"]);

const TOP_LEVEL_KEYS = [
  "schema",
  "schema_version",
  "tracker_version",
  "methodology_version",
  "window",
  "scope",
  "metrics",
  "leverage",
];
const WINDOW_KEYS = ["start", "end", "days", "timezone"];
const METRIC_KEYS = [
  "agent_hours_per_day",
  "total_agent_hours",
  "peak_day_agent_hours",
  "completed_turns",
  "active_days",
];

export class ScoreValidationError extends Error {
  constructor(message = "The public score is invalid.") {
    super(message);
    this.name = "ScoreValidationError";
  }
}

const invalid = (message = "The public score is invalid.") => {
  throw new ScoreValidationError(message);
};

const isObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function exactKeys(value, expected) {
  if (
    !isObject(value) ||
    Object.keys(value).length !== expected.length ||
    expected.some((key) => !Object.hasOwn(value, key))
  ) {
    invalid("The public score contains unsupported fields.");
  }
}

function finiteNonnegative(value, max, label) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > max
  ) {
    invalid(`The public score ${label} is outside the allowed range.`);
  }
  return value;
}

function parseDate(value, label) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    invalid(`The public score ${label} date is invalid.`);
  }
  const year = Number(value.slice(0, 4));
  if (year < 1970 || year > 9999) invalid(`The public score ${label} date is invalid.`);
  const timestamp = Date.UTC(
    year,
    Number(value.slice(5, 7)) - 1,
    Number(value.slice(8, 10)),
  );
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) {
    invalid(`The public score ${label} date is invalid.`);
  }
  return timestamp;
}

function dateString(timestamp) {
  return new Date(timestamp).toISOString().slice(0, 10);
}

function localDate(timestamp, timezone) {
  let formatter;
  try {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    invalid("The public score timezone is invalid.");
  }
  const parts = Object.fromEntries(
    formatter.formatToParts(timestamp).map(({ type, value }) => [type, value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function zonedMidnight(date, timezone) {
  const rough = parseDate(date, "eligibility");
  // Solving `UTC midnight - offset` is not stable when a zone skips or
  // repeats midnight during a DST transition. Search for the first instant
  // whose rendered local calendar date is the target (or a later date).
  let lower = rough - 2 * DAY_MS;
  let upper = rough + 2 * DAY_MS;
  if (localDate(lower, timezone) >= date) {
    lower = rough - 4 * DAY_MS;
  }
  if (localDate(upper, timezone) < date) {
    upper = rough + 4 * DAY_MS;
  }
  if (localDate(upper, timezone) < date) {
    invalid("The public score timezone is invalid.");
  }
  while (upper - lower > 1) {
    const middle = lower + Math.floor((upper - lower) / 2);
    if (localDate(middle, timezone) >= date) upper = middle;
    else lower = middle;
  }
  return upper;
}

/** Return the instant when this score is no longer a recent public window. */
export function scoreExpiryAt(score) {
  const end = parseDate(score.window.end, "end");
  return zonedMidnight(dateString(end + 9 * DAY_MS), score.window.timezone);
}

function normalNow(now) {
  const value = now instanceof Date ? now.getTime() : now;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    invalid("The public score validation clock is invalid.");
  }
  return value;
}

/**
 * Validate the exact aggregate score schema before a value reaches D1.
 * The optional clock is test-only dependency injection; production callers
 * use the current server clock.
 */
export function parseScore(input, { now = Date.now() } = {}) {
  let score;
  if (typeof input === "string") {
    if (new TextEncoder().encode(input).byteLength > MAX_SCORE_BYTES) {
      invalid("Public score JSON must be 32 KB or smaller.");
    }
    try {
      score = JSON.parse(input);
    } catch {
      invalid("The public score JSON could not be read.");
    }
  } else {
    score = input;
  }

  if (!isObject(score)) invalid();
  let encoded;
  try {
    encoded = JSON.stringify(score);
  } catch {
    invalid();
  }
  if (new TextEncoder().encode(encoded).byteLength > MAX_SCORE_BYTES) {
    invalid("Public score JSON must be 32 KB or smaller.");
  }

  exactKeys(score, TOP_LEVEL_KEYS);
  if (
    score.schema !== "agent-hours-score" ||
    score.schema_version !== 1 ||
    typeof score.tracker_version !== "string" ||
    !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(score.tracker_version) ||
    score.methodology_version !== "1" ||
    !SCOPES.has(score.scope) ||
    score.leverage !== null
  ) {
    invalid("The public score version, scope, or leverage is unsupported.");
  }

  exactKeys(score.window, WINDOW_KEYS);
  const start = parseDate(score.window.start, "start");
  const end = parseDate(score.window.end, "end");
  if (
    score.window.days !== 30 ||
    end - start !== 29 * DAY_MS ||
    typeof score.window.timezone !== "string" ||
    score.window.timezone.length === 0 ||
    score.window.timezone.length > 64
  ) {
    invalid("The public score window is invalid.");
  }

  const yesterday = parseDate(
    dateString(parseDate(localDate(normalNow(now), score.window.timezone), "today") - DAY_MS),
    "yesterday",
  );
  const ageInDays = Math.round((yesterday - end) / DAY_MS);
  if (ageInDays < 0 || ageInDays > 7) {
    invalid("The public score must end within the last eight completed days.");
  }

  exactKeys(score.metrics, METRIC_KEYS);
  const metrics = score.metrics;
  const agentHours = finiteNonnegative(
    metrics.agent_hours_per_day,
    MAX_HOURS_PER_DAY,
    "daily hours",
  );
  const totalHours = finiteNonnegative(
    metrics.total_agent_hours,
    MAX_TOTAL_HOURS,
    "total hours",
  );
  const peakHours = finiteNonnegative(
    metrics.peak_day_agent_hours,
    MAX_TOTAL_HOURS,
    "peak hours",
  );
  if (
    !Number.isSafeInteger(metrics.completed_turns) ||
    metrics.completed_turns < 0 ||
    metrics.completed_turns > MAX_TURNS ||
    !Number.isSafeInteger(metrics.active_days) ||
    metrics.active_days < 0 ||
    metrics.active_days > 30 ||
    metrics.active_days > metrics.completed_turns
  ) {
    invalid("The public score counts are invalid.");
  }
  if (
    !Number.isFinite(agentHours * 30) ||
    !Number.isFinite(peakHours * metrics.active_days) ||
    (metrics.active_days === 0 && totalHours > 0) ||
    peakHours * metrics.active_days + 0.000031 < totalHours ||
    Math.abs(agentHours * 30 - totalHours) > 0.000031 ||
    peakHours > totalHours + 0.000001 ||
    peakHours + 0.000001 < agentHours
  ) {
    invalid("The public score metrics are inconsistent.");
  }

  return score;
}

/** Stable JSON is used for idempotency comparisons independent of key order. */
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(",")}}`;
}

export async function scoreDigest(score) {
  const bytes = new TextEncoder().encode(canonicalJson(score));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return bytesToBase64Url(new Uint8Array(digest));
}

function bytesToBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}
