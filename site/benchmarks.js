"use strict";
const container = document.getElementById("benchmarks");
for (const item of globalThis.AGENT_HOURS_BENCHMARKS?.benchmarks || []) {
  const row = document.createElement("article");
  row.className = "benchmark-row";
  const value = document.createElement("div");
  value.className = "benchmark-value";
  value.textContent =
    (item.operator === ">" ? ">" : "") +
    item.value +
    (item.unit === "agent_hours_per_human_hour" ? "×" : "h");
  const detail = document.createElement("div"),
    title = document.createElement("h2");
  title.textContent = item.population;
  const meta = document.createElement("p");
  meta.className = "benchmark-meta";
  meta.textContent = `${item.unit.replaceAll("_", " ")} · ${item.period} · Published ${item.published_at}`;
  const caveat = document.createElement("p");
  caveat.textContent = Array.isArray(item.limitations)
    ? item.limitations.join(" ")
    : item.limitations;
  const source = document.createElement("a");
  source.className = "text-link";
  source.href = item.source_url;
  source.textContent = "Read the original OpenAI source ↗";
  detail.append(title, meta, caveat, source);
  row.append(value, detail);
  container.append(row);
}
