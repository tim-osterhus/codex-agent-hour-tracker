import { LocalScore, renderCard, number } from "./score.mjs?v=community-1";
const $ = (id) => document.getElementById(id);
const state = new LocalScore();
let mode = "upload",
  format = "landscape",
  revision = 0;
const uri = (svg) =>
  "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
function render() {
  $("empty-score").hidden = Boolean(state.score);
  $("score-result").hidden = !state.score;
  $("share-actions").hidden = !state.shareable;
  if (!state.score) {
    $("export-preview").removeAttribute("src");
    return;
  }
  const score = state.score;
  $("export-preview").src = uri(renderCard(score, format, !state.shareable));
  $("export-preview").alt =
    `${state.shareable ? "Self-reported, unverified score" : "Fabricated example"}: ${number(score.metrics.agent_hours_per_day)} agent hours per day; ${number(score.metrics.total_agent_hours)} total; ${score.window.start} through ${score.window.end}; ${score.scope}; ${score.window.timezone}.`;
  $("preview-state").textContent = state.shareable
    ? "SELF-REPORTED · UNVERIFIED"
    : "EXAMPLE · NOT MEASURED";
  $("download-status").textContent =
    `${format === "square" ? "1080 × 1080" : "1200 × 630"} · Saved on your machine.`;
  for (const choice of ["square", "landscape"])
    $(choice).setAttribute("aria-pressed", String(choice === format));
}
function clear() {
  revision++;
  state.clear();
  render();
}
function preview() {
  try {
    const agents = Number($("agent-count").value),
      hours = Number($("agent-hours").value);
    if (!$("agent-count").value || !$("agent-hours").value)
      throw new Error("Enter the number of agents and their daily hours.");
    state.preview(
      agents,
      hours,
      $("human").value.trim() ? Number($("human").value) : null,
    );
    $("preview-math").textContent =
      `${number(agents)} agents × ${number(hours)} hours = ${number(state.score.metrics.agent_hours_per_day)} agent hours / day.${state.score.leverage ? ` ${number(state.score.leverage.ratio)} agent hours for each of your hours.` : ""}`;
    $("import-status").textContent = "";
  } catch (error) {
    state.clear();
    $("preview-math").textContent = "";
    $("import-status").textContent = error.message;
  }
  render();
}
function selectMode(next) {
  mode = next;
  clear();
  for (const name of ["upload", "preview"]) {
    $(name + "-tab").setAttribute("aria-selected", String(name === mode));
    $(name + "-tab").tabIndex = name === mode ? 0 : -1;
    $(name + "-panel").hidden = name !== mode;
  }
  $("import-status").textContent = "";
  if (mode === "preview") preview();
}
for (const name of ["upload", "preview"]) {
  $(name + "-tab").addEventListener("click", () => selectMode(name));
  $(name + "-tab").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? "upload"
        : event.key === "End"
          ? "preview"
          : mode === "upload"
            ? "preview"
            : "upload";
    selectMode(next);
    $(next + "-tab").focus();
  });
}
$("copy-command").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("command").textContent);
    $("copy-status").textContent = "Copied. Run it in your terminal.";
  } catch {
    $("copy-status").textContent = "Select and copy the command above.";
  }
});
$("score-json").addEventListener("input", () => {
  clear();
  $("import-status").textContent = "Load this JSON to see your score.";
});
$("import").addEventListener("click", () => {
  clear();
  try {
    state.import($("score-json").value);
    $("import-status").textContent = "Score loaded. Nothing posted.";
    $("score-json").setAttribute("aria-invalid", "false");
  } catch (error) {
    $("import-status").textContent = error.message;
    $("score-json").setAttribute("aria-invalid", "true");
  }
  render();
});
$("score-file").addEventListener("change", async () => {
  clear();
  const current = revision,
    file = $("score-file").files[0];
  if (!file) return;
  try {
    // A slow file read must not replace a newer input.
    const imported = new LocalScore();
    await imported.importFile(file);
    if (current !== revision) return;
    state.import(JSON.stringify(imported.score));
    $("score-json").value = "";
    $("import-status").textContent =
      "Score loaded from your file. Nothing posted.";
  } catch (error) {
    if (current === revision) $("import-status").textContent = error.message;
  }
  if (current === revision) render();
});
for (const id of ["agent-count", "agent-hours", "human"])
  $(id).addEventListener("input", preview);
$("share-community").addEventListener("click", () => {
  try {
    sessionStorage.setItem("agent-hours-community-draft", state.handoff());
    location.assign("/community/");
  } catch {
    $("import-status").textContent =
      "Could not carry your score to the next page. Visit the community and load it there.";
  }
});
for (const choice of ["square", "landscape"])
  $(choice).addEventListener("click", () => {
    format = choice;
    render();
  });
function download(blob, extension) {
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = `agent-hours-${state.shareable ? state.score.window.end : "example"}-${format}.${extension}`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("download-svg").addEventListener("click", () => {
  if (!state.score) return;
  download(
    new Blob([renderCard(state.score, format, !state.shareable)], {
      type: "image/svg+xml",
    }),
    "svg",
  );
  $("download-status").textContent = "SVG saved locally.";
});
$("download-png").addEventListener("click", async () => {
  if (!state.score) return;
  const current = revision;
  $("download-png").disabled = true;
  try {
    const img = new Image();
    img.src = uri(renderCard(state.score, format, !state.shareable));
    await img.decode();
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d").drawImage(img, 0, 0);
    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!blob) throw new Error();
    if (current !== revision) return;
    download(blob, "png");
    $("download-status").textContent = "PNG saved locally.";
  } catch {
    $("download-status").textContent = "PNG could not be saved. Try SVG.";
  } finally {
    $("download-png").disabled = false;
  }
});
render();
