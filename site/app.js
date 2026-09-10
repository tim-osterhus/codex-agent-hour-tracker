import {
  EXAMPLE,
  parseScore,
  calculateLeverage,
  renderCard,
  number,
} from "./score.mjs";
const $ = (id) => document.getElementById(id);
let score = structuredClone(EXAMPLE),
  example = true,
  format = "landscape";
const uri = (svg) =>
  "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
function render() {
  const alt = `${example ? "Fabricated example" : "Self-reported, unverified"}: ${number(score.metrics.agent_hours_per_day)} agent hours per day; ${number(score.metrics.total_agent_hours)} total; peak day ${number(score.metrics.peak_day_agent_hours)} hours; ${score.metrics.completed_turns} completed turns; ${score.metrics.active_days} active days. ${score.window.start} through ${score.window.end}, ${score.window.timezone}, ${score.scope}. Tracker ${score.tracker_version}, methodology ${score.methodology_version}.${score.leverage ? ` Leverage ${number(score.leverage.ratio)} times, using ${number(score.leverage.human_hours)} human hours, ${score.leverage.basis === "estimated-weekly" ? `estimated from ${number(score.leverage.human_hours_per_week)} hours per week` : "reported for this period"}.` : ""}`;
  $("score-preview").src = uri(renderCard(score, "landscape", example));
  $("export-preview").src = uri(renderCard(score, format, example));
  $("export-preview").alt = $("score-preview").alt = alt;
  $("preview-state").textContent = example
    ? "FABRICATED EXAMPLE"
    : "SELF-REPORTED · UNVERIFIED";
  $("download-status").textContent =
    `${format === "square" ? "1080 × 1080" : "1200 × 630"} · ${example ? "Example is labeled on the image." : "Self-reported provenance stays on the image."}`;
  for (const choice of ["square", "landscape"])
    $(choice).setAttribute("aria-pressed", String(choice === format));
}
$("copy-command").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("command").textContent);
    $("copy-status").textContent = "Command copied. Run it in your terminal.";
  } catch {
    $("copy-status").textContent =
      "Clipboard unavailable. Select and copy the command above.";
  }
});
$("import").addEventListener("click", () => {
  try {
    score = parseScore($("score-json").value);
    example = false;
    render();
    $("import-status").textContent =
      "Public score accepted. Preview updated; nothing uploaded.";
    $("score-json").setAttribute("aria-invalid", "false");
  } catch (error) {
    $("import-status").textContent =
      error.message + " Previous preview retained.";
    $("score-json").setAttribute("aria-invalid", "true");
  }
});
$("example").addEventListener("click", () => {
  score = structuredClone(EXAMPLE);
  example = true;
  $("score-json").value = "";
  $("score-json").removeAttribute("aria-invalid");
  $("import-status").textContent = "Showing fabricated example data.";
  render();
});
for (const choice of ["square", "landscape"])
  $(choice).addEventListener("click", () => {
    format = choice;
    render();
  });
function filename(extension) {
  return `agent-hours-${example ? "example" : score.window.end}-${format}.${extension}`;
}
function download(blob, extension, name = filename(extension)) {
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("download-svg").addEventListener("click", () => {
  download(
    new Blob([renderCard(score, format, example)], { type: "image/svg+xml" }),
    "svg",
  );
  $("download-status").textContent = "SVG download created locally.";
});
$("download-png").addEventListener("click", async () => {
  const button = $("download-png");
  button.disabled = true;
  try {
    const name = filename("png"),
      snapshot = renderCard(score, format, example),
      img = new Image();
    img.src = uri(snapshot);
    await img.decode();
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d").drawImage(img, 0, 0);
    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!blob) throw new Error();
    download(blob, "png", name);
    $("download-status").textContent = "PNG download created locally.";
  } catch {
    $("download-status").textContent =
      "PNG could not be rendered in this browser. Try the SVG download.";
  } finally {
    button.disabled = false;
  }
});
function calculator() {
  const runtime = $("runtime").valueAsNumber,
    weekly = $("human").valueAsNumber,
    result = calculateLeverage(runtime, weekly);
  $("runtime").setAttribute(
    "aria-invalid",
    String(!Number.isFinite(runtime) || runtime < 0),
  );
  $("human").setAttribute(
    "aria-invalid",
    String(!Number.isFinite(weekly) || weekly <= 0),
  );
  document
    .querySelectorAll("[data-human]")
    .forEach((b) =>
      b.setAttribute(
        "aria-pressed",
        String(Number(b.dataset.human) === weekly),
      ),
    );
  if (!result) {
    $("benchmark-bars").hidden = true;
    $("reference-ratio").textContent = "";
    $("ratio").textContent = "—";
    $("ratio-description").textContent =
      "Enter finite runtime of zero or more and human hours greater than zero.";
    $("equation").textContent = "Both inputs are required.";
    return;
  }
  $("ratio").textContent = number(result.ratio) + "×";
  $("ratio-description").textContent =
    `${number(result.ratio)} agent-hours for each human hour.`;
  $("equation").textContent =
    `${number(result.total)} agent hours ÷ ${number(result.human)} estimated human hours`;
  if (reference) {
    $("reference-ratio").textContent =
      `${number(result.ratio / reference.value)}× the published reference ratio (${number(result.ratio)} ÷ ${reference.value}).`;
    const scale = 420 / Math.max(result.ratio, reference.value);
    $("benchmark-bars").hidden = false;
    $("benchmark-bars").src = uri(
      `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="104"><g font-family="Arial, sans-serif" font-size="12" fill="#202320"><text x="0" y="14">Your scenario · ${number(result.ratio)}×</text><text x="0" y="67">OpenAI research organization · ${reference.value}×</text></g><rect y="26" width="${result.ratio * scale}" height="10" fill="#d95239"/><rect y="79" width="${reference.value * scale}" height="10" fill="#737969"/></svg>`,
    );
    $("benchmark-bars").alt =
      `Your estimated scenario ${number(result.ratio)} times; OpenAI research organization ${reference.value} times. Different populations and human-hour denominators.`;
  }
}
for (const id of ["runtime", "human"])
  $(id).addEventListener("input", calculator);
document.querySelectorAll("[data-human]").forEach((b) =>
  b.addEventListener("click", () => {
    $("human").value = b.dataset.human;
    calculator();
  }),
);
$("agent-count").addEventListener("input", () => {
  const count = Number($("agent-count").value);
  $("agent-count-value").value = count;
  $("parallel-hours").textContent =
    `${count} agent-hour${count === 1 ? "" : "s"}.`;
  $("lanes").setAttribute(
    "aria-label",
    `${count} agents each working one hour`,
  );
  document
    .querySelectorAll(".lane")
    .forEach((lane, i) => (lane.hidden = i >= count));
});
const motion = matchMedia("(prefers-reduced-motion: reduce)");
function setPaused(paused) {
  paused = motion.matches || paused;
  $("lanes").classList.toggle("paused", paused);
  $("pause").setAttribute("aria-pressed", String(paused));
  $("pause").disabled = motion.matches;
  $("pause").textContent = motion.matches
    ? "Reduced motion on"
    : paused
      ? "Resume motion"
      : "Pause motion";
}
setPaused(motion.matches);
motion.addEventListener("change", (e) => setPaused(e.matches));
$("pause").addEventListener("click", () =>
  setPaused($("pause").getAttribute("aria-pressed") !== "true"),
);
const reference = globalThis.AGENT_HOURS_BENCHMARKS?.benchmarks.find(
  (b) => b.id === "openai-research-2026-08",
);
if (reference)
  $("benchmark-context").textContent =
    `Published context: ${reference.value}× for OpenAI’s research team (${reference.period}). Different population; not an individual target or percentile.`;
render();
calculator();
