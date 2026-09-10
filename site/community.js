import {
  LocalScore,
  parseScore,
  number,
  quotaNotice,
} from "./score.mjs?v=community-1";
const $ = (id) => document.getElementById(id);
const state = new LocalScore(),
  draftKey = "agent-hours-community-draft";
let config = { enabled: false },
  session = { authenticated: false },
  scope = "interactive-only";
let revision = 0,
  boardRevision = 0,
  token = null,
  widget = null,
  requestId = null,
  posting = false,
  intending = false;
async function api(path, options = {}) {
  const response = await fetch("/api/community/" + path, {
    credentials: "same-origin",
    ...options,
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.message === "string"
        ? data.message
        : "That request did not work. Please try again.",
    );
  return data;
}
function mutation(method, body) {
  return {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
    },
    body: JSON.stringify(body),
  };
}
function allowed() {
  return (
    config.enabled === true &&
    typeof config.turnstile_site_key === "string" &&
    Boolean(
      session.authenticated &&
        session.csrf_token &&
        session.quota &&
        session.quota.remaining > 0 &&
        state.shareable,
    ) &&
    !posting
  );
}
function cancel() {
  intending = false;
  token = null;
  if (widget !== null && globalThis.turnstile)
    globalThis.turnstile.remove(widget);
  widget = null;
  $("confirmation").hidden = true;
  $("post-score").hidden = true;
  $("post-score").disabled = true;
}
function render() {
  for (const id of [
    "score-json",
    "score-file",
    "import",
    "clear-draft",
    "cancel-post",
  ])
    $(id).disabled = posting;
  $("draft-review").hidden = !state.shareable;
  $("public-fields").textContent = state.shareable
    ? JSON.stringify(JSON.parse(state.handoff()), null, 2)
    : "";
  $("score-summary").replaceChildren();
  if (state.shareable) {
    const score = state.score,
      metrics = score.metrics;
    const fields = [
      ["Agent hours / day", metrics.agent_hours_per_day],
      ["Total agent hours", metrics.total_agent_hours],
      ["Peak day", `${metrics.peak_day_agent_hours} hours`],
      ["Completed turns", metrics.completed_turns],
      ["Active days", `${metrics.active_days} of ${score.window.days} days`],
      ["Date window", `${score.window.start} to ${score.window.end}`],
      ["Timezone", score.window.timezone],
      [
        "Sessions",
        score.scope === "interactive-only"
          ? "Interactive only"
          : "Including batch / exec",
      ],
      ["Tracker version", score.tracker_version],
      ["Method version", score.methodology_version],
    ];
    for (const [label, value] of fields) {
      const term = document.createElement("dt"),
        detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = String(value);
      $("score-summary").append(term, detail);
    }
  }
  $("identity").textContent = session.authenticated
    ? `Posting as ${session.user.login}`
    : "Sign in to own your post.";
  $("sign-in").hidden = session.authenticated || !config.enabled;
  $("intend-post").disabled = !allowed();
  $("quota-status").textContent = quotaNotice(session.quota);
  $("account").hidden = !session.authenticated;
}
function clear() {
  revision++;
  state.clear();
  requestId = null;
  cancel();
  try {
    sessionStorage.removeItem(draftKey);
  } catch {
    /* Local preview still works without storage. */
  }
  render();
}
function accept(text) {
  clear();
  state.import(text);
  // Only the score without human-hour data crosses the community boundary.
  state.import(state.handoff());
  requestId = crypto.randomUUID();
  $("import-status").textContent =
    "Score loaded locally. Review the public fields before posting.";
  render();
}
$("score-json").addEventListener("input", () => {
  clear();
  $("import-status").textContent = "Load this JSON to review it.";
});
$("import").addEventListener("click", () => {
  try {
    accept($("score-json").value);
    $("score-json").setAttribute("aria-invalid", "false");
  } catch (error) {
    $("import-status").textContent = error.message;
    $("score-json").setAttribute("aria-invalid", "true");
  }
});
$("clear-draft").addEventListener("click", () => {
  clear();
  $("score-json").value = "";
  $("score-file").value = "";
  $("import-status").textContent = "Score cleared from this tab.";
});
$("score-file").addEventListener("change", async () => {
  clear();
  const current = revision,
    file = $("score-file").files[0];
  if (!file) return;
  try {
    const imported = new LocalScore();
    await imported.importFile(file);
    if (current !== revision) return;
    accept(imported.handoff());
    $("score-json").value = "";
  } catch (error) {
    if (current === revision) $("import-status").textContent = error.message;
  }
});
$("sign-in").addEventListener("click", (event) => {
  try {
    sessionStorage.setItem(draftKey, state.handoff());
  } catch {
    event.preventDefault();
    $("post-status").textContent =
      "This browser cannot keep your score through sign-in. Allow tab storage and try again.";
  }
});
async function loadChallenge() {
  if (globalThis.turnstile) return;
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src =
      "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.async = true;
    script.onload = resolve;
    script.onerror = () => {
      script.remove();
      reject(new Error("The check could not load. Please try again."));
    };
    document.head.append(script);
  });
}
$("intend-post").addEventListener("click", async () => {
  if (!allowed() || intending) return;
  intending = true;
  const current = revision;
  $("confirmation").hidden = false;
  $("post-status").textContent = "Loading the check…";
  try {
    await loadChallenge();
    if (current !== revision || !intending || !allowed()) return;
    widget = globalThis.turnstile.render($("turnstile"), {
      sitekey: config.turnstile_site_key,
      action: "community_post",
      callback: (value) => {
        if (current !== revision || !intending) return;
        token = value;
        $("post-score").disabled = !allowed();
        $("post-status").textContent =
          "Check complete. Choose Post my score to share.";
      },
      "expired-callback": () => {
        token = null;
        $("post-score").disabled = true;
        $("post-status").textContent =
          "The check expired. Cancel and try again.";
      },
      "error-callback": () => {
        token = null;
        $("post-score").disabled = true;
        $("post-status").textContent =
          "The check failed. Cancel and try again.";
      },
    });
    $("post-score").hidden = false;
    $("post-status").textContent = "Complete the check to enable posting.";
  } catch (error) {
    cancel();
    $("post-status").textContent = error.message;
  }
});
$("cancel-post").addEventListener("click", () => {
  cancel();
  $("post-status").textContent = "Nothing posted.";
});
$("post-score").addEventListener("click", async () => {
  if (!allowed() || !intending || !token) return;
  const body = {
    score: JSON.parse(state.handoff()),
    turnstile_token: token,
    request_id: requestId,
  };
  posting = true;
  $("post-score").disabled = true;
  render();
  $("post-status").textContent = "Posting…";
  try {
    const result = await api("scores", mutation("POST", body));
    session.quota = result.quota;
    clear();
    $("score-json").value = "";
    $("score-file").value = "";
    $("import-status").textContent =
      "Your score is public. You can delete it below.";
    await Promise.allSettled([loadBoard({ fresh: true }), loadSession()]);
  } catch (error) {
    cancel();
    $("post-status").textContent =
      error.message +
      " Your local score is still here. Try again to check the same post.";
    await loadSession().catch(() => {});
  } finally {
    posting = false;
    render();
  }
});
async function loadBoard({ fresh = false } = {}) {
  const current = ++boardRevision;
  $("board-status").textContent = "Loading scores…";
  $("score-table").hidden = true;
  $("score-rows").replaceChildren();
  try {
    const data = await api(
      "scores?scope=" + scope,
      fresh ? { cache: "no-store" } : {},
    );
    if (current !== boardRevision) return;
    if (!Array.isArray(data.scores)) throw new Error();
    for (const row of data.scores.slice(0, 50)) {
      const score = parseScore(JSON.stringify(row.score));
      if (score.scope !== scope) continue;
      const tr = document.createElement("tr");
      for (const value of [
        row.login,
        number(score.metrics.agent_hours_per_day),
        number(score.metrics.total_agent_hours),
        score.window.end,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        tr.append(cell);
      }
      $("score-rows").append(tr);
    }
    const count = $("score-rows").children.length;
    $("score-table").hidden = count === 0;
    $("board-status").textContent = count ? "" : "No scores in this list yet.";
  } catch {
    if (current === boardRevision)
      $("board-status").textContent =
        "The community list is unavailable. You can still load a score below.";
  }
}
for (const choice of ["interactive-only", "including-exec"])
  $(choice).addEventListener("click", () => {
    scope = choice;
    for (const id of ["interactive-only", "including-exec"])
      $(id).setAttribute("aria-pressed", String(scope === id));
    loadBoard();
  });
async function loadSession() {
  session = await api("session");
  $("own-scores").replaceChildren();
  for (const row of session.scores || []) {
    const item = document.createElement("div"),
      label = document.createElement("p"),
      button = document.createElement("button");
    label.textContent = `${row.score.scope}: ${number(row.score.metrics.agent_hours_per_day)} hours / day`;
    button.type = "button";
    button.textContent = `Delete my ${row.score.scope} score`;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api("scores", mutation("DELETE", { scope: row.score.scope }));
        $("account-status").textContent =
          "Score deleted. Your post limit stays the same.";
        await Promise.all([loadSession(), loadBoard({ fresh: true })]);
      } catch (error) {
        $("account-status").textContent = error.message;
        button.disabled = false;
      }
    });
    item.append(label, button);
    $("own-scores").append(item);
  }
  render();
}
$("logout").addEventListener("click", async () => {
  try {
    await api("logout", mutation("POST", {}));
    session = { authenticated: false };
    cancel();
    render();
    $("account-status").textContent = "Signed out.";
  } catch (error) {
    $("account-status").textContent = error.message;
  }
});
try {
  const draft = sessionStorage.getItem(draftKey);
  if (draft) accept(draft);
} catch {
  clear();
  $("import-status").textContent =
    "That saved score could not be loaded. Choose your JSON again.";
}
render();
loadBoard();
Promise.all([api("config"), loadSession()])
  .then(([value]) => {
    config = value;
    $("service-status").textContent = config.enabled
      ? "Posting is available. Review your score, then sign in."
      : "New posts are paused. You can still review your score here.";
    render();
  })
  .catch(() => {
    config = { enabled: false };
    $("service-status").textContent =
      "Posting is unavailable. Your local score preview still works.";
    render();
  });
