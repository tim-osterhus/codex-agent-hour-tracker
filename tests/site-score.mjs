import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  EXAMPLE,
  parseScore,
  calculateLeverage,
  renderCard,
} from "../site/score.mjs";
test("legacy asset URLs bypass cached pre-0.2 website code", () => {
  for (const path of ["../site/index.html", "../site/benchmarks/index.html"]) {
    const html = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(html, /href="\/styles\.css\?v=0\.2\.0"/);
    assert.doesNotMatch(html, /(?:src|href)="\/(?:app\.js|styles\.css)"/);
    if (path === "../site/index.html") {
      assert.match(html, /src="\/app\.js\?v=0\.2\.0"/);
    }
  }
});
const changed = (fn) => {
  const value = structuredClone(EXAMPLE);
  fn(value);
  return JSON.stringify(value);
};
test("coherent synthetic score roundtrips with exact public fields", () =>
  assert.deepEqual(parseScore(JSON.stringify(EXAMPLE)), EXAMPLE));
test("private archives, unknown nested fields, oversized input and invalid JSON rejected", () => {
  for (const input of [
    JSON.stringify({ schema: "agent-hours-archive", observations: [] }),
    changed((s) => (s.path = "/private")),
    changed((s) => (s.metrics.daily = [])),
    changed((s) => (s.window.id = "secret")),
    " ".repeat(32769),
    "{",
  ])
    assert.throws(() => parseScore(input));
});
test("finite, typed metrics; calendar bounds; supported timezone and versions enforced", () => {
  for (const modify of [
    (s) => (s.metrics.total_agent_hours = -1),
    (s) => (s.metrics.active_days = 31),
    (s) => (s.metrics.completed_turns = 1.5),
    (s) => (s.metrics.agent_hours_per_day = "12.4"),
    (s) => (s.window.end = "2026-09-09"),
    (s) => (s.window.start = "2026-02-30"),
    (s) => (s.window.timezone = "<script>"),
    (s) => (s.methodology_version = "2"),
    (s) => (s.schema_version = 2),
    (s) => (s.scope = "private"),
    (s) => (s.metrics.peak_day_agent_hours = 400),
  ])
    assert.throws(() => parseScore(changed(modify)));
  assert.throws(() =>
    parseScore(JSON.stringify(EXAMPLE).replace("372", "1e999")),
  );
});
test("leverage allows consistent reported and weekly assumptions", () => {
  const reported = changed(
    (s) =>
      (s.leverage = {
        ratio: 3.72,
        human_hours: 100,
        basis: "reported-period",
        human_hours_per_week: null,
      }),
  );
  assert.equal(parseScore(reported).leverage.ratio, 3.72);
  const estimated = changed(
    (s) =>
      (s.leverage = {
        ratio: 2.17,
        human_hours: 171.428571,
        basis: "estimated-weekly",
        human_hours_per_week: 40,
      }),
  );
  assert.equal(parseScore(estimated).leverage.basis, "estimated-weekly");
  assert.throws(() =>
    parseScore(
      changed(
        (s) =>
          (s.leverage = {
            ratio: 3,
            human_hours: 0,
            basis: "reported-period",
            human_hours_per_week: null,
          }),
      ),
    ),
  );
});
test("calculator uses the same 30 days and handles zero without accepting invalid denominator", () => {
  assert.equal(calculateLeverage(12.4, 40).ratio, 2.1700000000000004);
  assert.equal(calculateLeverage(0, 40).ratio, 0);
  for (const values of [
    [10, 0],
    [-1, 40],
    [Infinity, 40],
    [10, NaN],
    [Number.MAX_VALUE, 1],
  ])
    assert.equal(calculateLeverage(...values), null);
});
test("both downloadable renderings retain provenance and score context", () => {
  for (const format of ["landscape", "square"]) {
    const svg = renderCard(EXAMPLE, format, true);
    for (const text of [
      "FABRICATED EXAMPLE",
      "2026-08-10",
      "2026-09-08",
      "UTC",
      "interactive-only",
      "TRACKER 0.2.0 / METHOD 1",
      "12.4",
      "372",
    ])
      assert.ok(svg.includes(text), text);
    assert.ok(
      svg.includes(
        format === "square"
          ? 'width="1080" height="1080"'
          : 'width="1200" height="630"',
      ),
    );
    assert.ok(!/<script|foreignObject|href=/i.test(svg));
  }
  assert.ok(renderCard(EXAMPLE, "square", false).includes("SELF-REPORTED"));
});
test("rendered text is escaped even outside validation", () => {
  const value = structuredClone(EXAMPLE);
  value.window.timezone = "<script>bad()</script>";
  assert.ok(!renderCard(value).includes("<script>"));
});
