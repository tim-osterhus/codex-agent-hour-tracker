// Public aggregates only. This module is also exercised directly by Node tests.
export const EXAMPLE = {
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
    agent_hours_per_day: 12.4,
    total_agent_hours: 372,
    peak_day_agent_hours: 28.6,
    completed_turns: 486,
    active_days: 26,
  },
  leverage: null,
};
const fail = () => {
  throw new Error(
    "Use a valid public agent-hours-score JSON export. Private archives, unknown fields, and invalid values are not accepted.",
  );
};
function keys(value, expected) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== expected.length ||
    expected.some((key) => !Object.hasOwn(value, key))
  )
    fail();
}
const nonnegative = (x) =>
  typeof x === "number" && Number.isFinite(x) && x >= 0;
function date(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) fail();
  const time = Date.parse(value + "T00:00:00Z");
  if (
    !Number.isFinite(time) ||
    new Date(time).toISOString().slice(0, 10) !== value
  )
    fail();
  return time;
}
export function parseScore(text) {
  if (typeof text !== "string" || new TextEncoder().encode(text).length > 32768)
    throw new Error("Public score JSON must be 32 KB or smaller.");
  let score;
  try {
    score = JSON.parse(text);
  } catch {
    throw new Error(
      "Could not read JSON. Paste the complete output of the --share --format json command.",
    );
  }
  keys(score, [
    "schema",
    "schema_version",
    "tracker_version",
    "methodology_version",
    "window",
    "scope",
    "metrics",
    "leverage",
  ]);
  if (
    score.schema !== "agent-hours-score" ||
    score.schema_version !== 1 ||
    typeof score.tracker_version !== "string" ||
    !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(score.tracker_version) ||
    score.methodology_version !== "1"
  )
    fail();
  keys(score.window, ["start", "end", "days", "timezone"]);
  if (
    score.window.days !== 30 ||
    date(score.window.end) - date(score.window.start) !== 29 * 86400000 ||
    typeof score.window.timezone !== "string" ||
    score.window.timezone.length > 64
  )
    fail();
  try {
    new Intl.DateTimeFormat("en", { timeZone: score.window.timezone });
  } catch {
    fail();
  }
  if (!["interactive-only", "including-exec"].includes(score.scope)) fail();
  const m = score.metrics;
  keys(m, [
    "agent_hours_per_day",
    "total_agent_hours",
    "peak_day_agent_hours",
    "completed_turns",
    "active_days",
  ]);
  if (
    !Object.values(m).every(nonnegative) ||
    !Number.isSafeInteger(m.completed_turns) ||
    !Number.isSafeInteger(m.active_days) ||
    m.active_days > 30 ||
    m.active_days > m.completed_turns
  )
    fail();
  if (
    !Number.isFinite(m.agent_hours_per_day * 30) ||
    !Number.isFinite(m.peak_day_agent_hours * m.active_days) ||
    (m.active_days === 0 && m.total_agent_hours > 0) ||
    m.peak_day_agent_hours * m.active_days + 0.000031 < m.total_agent_hours ||
    Math.abs(m.agent_hours_per_day * 30 - m.total_agent_hours) > 0.000031 ||
    m.peak_day_agent_hours > m.total_agent_hours + 0.000001 ||
    m.peak_day_agent_hours + 0.000001 < m.agent_hours_per_day
  )
    fail();
  if (score.leverage !== null) {
    const l = score.leverage;
    keys(l, ["ratio", "human_hours", "basis", "human_hours_per_week"]);
    if (
      !nonnegative(l.ratio) ||
      !nonnegative(l.human_hours) ||
      l.human_hours <= 0 ||
      !["reported-period", "estimated-weekly"].includes(l.basis)
    )
      fail();
    if (l.basis === "reported-period" && l.human_hours_per_week !== null)
      fail();
    if (
      l.basis === "estimated-weekly" &&
      (!nonnegative(l.human_hours_per_week) ||
        l.human_hours_per_week <= 0 ||
        Math.abs((l.human_hours_per_week * 30) / 7 - l.human_hours) > 0.000003)
    )
      fail();
    // Exported totals and denominators are rounded independently to six decimals.
    if (
      !Number.isFinite(l.ratio * l.human_hours) ||
      !Number.isFinite(l.human_hours + l.ratio) ||
      Math.abs(l.ratio * l.human_hours - m.total_agent_hours) >
        0.000002 + 0.000001 * (l.human_hours + l.ratio)
    )
      fail();
  }
  return score;
}
export function calculateLeverage(runtime, weekly) {
  if (!nonnegative(runtime) || !Number.isFinite(weekly) || weekly <= 0)
    return null;
  const total = runtime * 30,
    human = (weekly * 30) / 7,
    ratio = total / human;
  return [total, human, ratio].every(Number.isFinite) && human > 0
    ? { total, human, ratio }
    : null;
}
export const number = (n) =>
  Math.abs(n) >= 1e7
    ? n.toExponential(2)
    : n.toLocaleString("en-US", { maximumFractionDigits: 2 });
const escape = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
      })[c],
  );
export function renderCard(score, format = "landscape", example = false) {
  const square = format === "square",
    width = square ? 1080 : 1200,
    height = square ? 1080 : 630;
  const m = score.metrics,
    l = score.leverage,
    left = 60,
    right = width - 60;
  const text = (
    x,
    y,
    size,
    content,
    color = "#f0eee5",
    family = "Arial, Helvetica, sans-serif",
    extra = "",
  ) =>
    `<text x="${x}" y="${y}" fill="${color}" font-family="${family}" font-size="${size}" ${extra}>${escape(content)}</text>`;
  const line = (y) => `<path d="M60 ${y} H${right}" stroke="#42483f"/>`;
  const scoreY = square ? 390 : 280,
    statsY = square ? 560 : 388,
    footY = square ? 820 : 483;
  const val = number(m.agent_hours_per_day),
    size = Math.min(
      square ? 210 : 176,
      (width - 130) / Math.max(val.length * 0.62, 1),
    );
  const metadata = `${score.window.start} — ${score.window.end} · 30 completed days`;
  const provenance = example
    ? "FABRICATED EXAMPLE"
    : "SELF-REPORTED · UNVERIFIED";
  const leverageText = l
    ? `${number(l.ratio)}× leverage · ${number(l.human_hours)} human hours / period`
    : "Cumulative runtime. Parallel turns add together.";
  const basis = l
    ? l.basis === "estimated-weekly"
      ? `Estimated from ${number(l.human_hours_per_week)} human h/week × 30 ÷ 7`
      : "Human hours reported for this 30-day period"
    : "Runtime is not a measure of output or time saved.";
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="${width}" height="${height}" fill="#202320"/><rect x="0" y="0" width="8" height="${height}" fill="#ff795e"/>`;
  svg +=
    text(left, 65, 26, "≋ agent hours") +
    text(
      right,
      64,
      15,
      provenance,
      "#ff795e",
      "monospace",
      'text-anchor="end"',
    ) +
    line(94);
  svg += text(
    left,
    square ? 168 : 141,
    16,
    "AGENT HOURS / DAY",
    "#abb0a6",
    "monospace",
  );
  svg += text(
    left - 8,
    scoreY,
    size,
    val,
    "#f0eee5",
    "monospace",
    'letter-spacing="-9"',
  );
  const laneX = square ? 700 : 890,
    laneY = square ? 205 : 185;
  if (val.length < 7)
    for (let i = 0; i < 3; i++)
      svg += `<rect x="${laneX}" y="${laneY + i * 25}" width="${right - laneX}" height="8" fill="#ff795e" opacity="${1 - i * 0.22}"/>`;
  svg += text(left, scoreY + 40, 17, metadata, "#abb0a6") + line(statsY - 51);
  const stats = [
    ["TOTAL AGENT HOURS", number(m.total_agent_hours)],
    ["PEAK DAY / HOURS", number(m.peak_day_agent_hours)],
    ["COMPLETED TURNS", number(m.completed_turns)],
  ];
  stats.forEach(([label, value], i) => {
    const x = left + (i * (width - 120)) / 3;
    svg +=
      text(x, statsY - 17, 13, label, "#abb0a6", "monospace") +
      text(x, statsY + 26, 34, value, "#f0eee5", "monospace");
  });
  svg +=
    line(statsY + 55) +
    text(left, footY, 17, leverageText) +
    text(left, footY + 29, 15, basis, "#abb0a6");
  svg += text(
    left,
    footY + 61,
    14,
    `${score.window.timezone} · ${score.scope} · ${m.active_days} active days`,
    "#abb0a6",
    "monospace",
  );
  svg +=
    text(
      left,
      height - 35,
      13,
      `TRACKER ${score.tracker_version} / METHOD ${score.methodology_version}`,
      "#abb0a6",
      "monospace",
    ) +
    text(
      right,
      height - 35,
      15,
      "agenthours.dev",
      "#f0eee5",
      "monospace",
      'text-anchor="end"',
    );
  return svg + "</svg>";
}
