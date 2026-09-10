import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { parseHTML } from "linkedom";
import * as score from "../site/score.mjs";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function page(community = false, options = {}) {
  const { document, window } = parseHTML(
    readFileSync(
      new URL(
        community ? "../site/community/index.html" : "../site/index.html",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  const storage = new Map(
      options.draft
        ? [["agent-hours-community-draft", JSON.stringify(options.draft)]]
        : [],
    ),
    calls = [];
  const context = vm.createContext({
    document,
    ...score,
    console,
    Blob,
    URL,
    setTimeout,
    clearTimeout,
    TextEncoder,
    crypto: { randomUUID: () => "12345678-1234-4123-8123-123456789abc" },
    sessionStorage: {
      setItem: (k, v) => storage.set(k, v),
      getItem: (k) => storage.get(k),
      removeItem: (k) => storage.delete(k),
    },
    location: { assign: (url) => calls.push({ navigate: url }) },
    fetch: async (url, init = {}) => {
      calls.push({ url, ...init });
      if (options.offline) throw new Error("offline");
      if (init.method === "POST" && options.postResponse)
        return options.postResponse();
      return {
        ok: true,
        json: async () =>
          url.endsWith("config")
            ? { enabled: true, turnstile_site_key: "test-site-key" }
            : url.endsWith("session")
              ? {
                  authenticated: true,
                  user: { login: "tester" },
                  csrf_token: "csrf-test",
                  quota: options.quota || {
                    used: 0,
                    remaining: 5,
                    next_slot_at: null,
                  },
                  scores: options.ownScores || [],
                }
              : url.includes("?scope=")
                ? { scores: options.rows || [] }
                : {
                    ok: true,
                    quota: { used: 1, remaining: 4, next_slot_at: null },
                  },
      };
    },
    turnstile: {
      render: (_node, config) => {
        context.challenge = config;
        return "widget";
      },
      remove: () => {},
    },
  });
  const source = readFileSync(
    new URL(
      community ? "../site/community.js" : "../site/app.js",
      import.meta.url,
    ),
    "utf8",
  ).replace(/^import .*?;\n/s, "");
  vm.runInContext(source, context);
  await tick();
  return {
    document,
    context,
    storage,
    calls,
    $: (id) => document.getElementById(id),
    fire: (id, type = "click", props = {}) =>
      document
        .getElementById(id)
        .dispatchEvent(Object.assign(new window.Event(type), props)),
    tick,
  };
}
test("homepage starts local and empty; imported scores need a share click for tab handoff", async () => {
  const p = await page();
  assert.equal(p.$("nerds").hasAttribute("open"), false);
  assert.equal(p.$("upload-tab").getAttribute("aria-selected"), "true");
  assert.equal(p.$("score-result").hidden, true);
  p.$("score-json").value = JSON.stringify(score.EXAMPLE);
  p.fire("import");
  assert.equal(p.$("share-actions").hidden, false);
  assert.equal(p.storage.size, 0);
  assert.equal(p.calls.length, 0);
  p.fire("share-community");
  assert.equal(p.storage.size, 1);
  assert.deepEqual(p.calls, [{ navigate: "/community/" }]);
  p.$("score-json").value = "{";
  p.fire("import");
  assert.equal(p.$("share-actions").hidden, true);
  assert.equal(p.$("score-result").hidden, true);
});
test("demo tab computes one combined card and never offers publishing", async () => {
  const p = await page();
  p.fire("preview-tab");
  assert.equal(p.$("upload-panel").hidden, true);
  assert.match(p.$("preview-math").textContent, /12 agent hours/);
  assert.equal(p.$("share-actions").hidden, true);
  p.fire("share-community");
  assert.equal(p.storage.size, 0);
  assert.equal(p.calls.length, 0);
});
test("community draft is only posted after challenge and explicit final confirmation", async () => {
  const p = await page(true, { draft: score.EXAMPLE });
  assert.equal(p.$("post-score").hidden, true);
  assert.equal(
    p.calls.some((c) => c.method === "POST"),
    false,
  );
  p.fire("intend-post");
  await p.tick();
  p.context.challenge.callback("challenge-token");
  assert.equal(p.$("post-score").disabled, false);
  assert.equal(
    p.calls.some((c) => c.method === "POST"),
    false,
  );
  p.fire("post-score");
  await p.tick();
  const request = p.calls.find((c) => c.method === "POST");
  assert.equal(request.url, "/api/community/scores");
  assert.equal(request.headers["X-CSRF-Token"], "csrf-test");
  const body = JSON.parse(request.body);
  assert.equal(body.score.leverage, null);
  assert.equal(body.turnstile_token, "challenge-token");
  assert.equal(p.storage.size, 0);
});
test("API outage leaves local community preview working", async () => {
  const p = await page(true, { offline: true });
  p.$("score-json").value = JSON.stringify(score.EXAMPLE);
  p.fire("import");
  assert.equal(p.$("draft-review").hidden, false);
  assert.equal(p.$("intend-post").disabled, true);
  assert.match(p.$("service-status").textContent, /unavailable/);
});
test("community review shows readable public fields while exact JSON starts closed", async () => {
  const p = await page(true, { draft: score.EXAMPLE });
  const summary = p.$("draft-review").querySelector("dl");
  assert.ok(summary, "Public fields need a readable summary");
  assert.deepEqual(
    Array.from(summary.querySelectorAll("dt"), (node) => node.textContent),
    [
      "Agent hours / day",
      "Total agent hours",
      "Peak day",
      "Completed turns",
      "Active days",
      "Date window",
      "Timezone",
      "Sessions",
      "Tracker version",
      "Method version",
    ],
  );
  assert.deepEqual(
    Array.from(summary.querySelectorAll("dd"), (node) => node.textContent),
    [
      "12.4",
      "372",
      "28.6 hours",
      "486",
      "26 of 30 days",
      "2026-08-10 to 2026-09-08",
      "UTC",
      "Interactive only",
      "0.2.0",
      "1",
    ],
  );
  const disclosure = p.$("public-fields").closest("details");
  assert.ok(disclosure, "Exact JSON belongs in a disclosure");
  assert.equal(disclosure.hasAttribute("open"), false);
  assert.deepEqual(JSON.parse(p.$("public-fields").textContent), score.EXAMPLE);
});
test("fifth accepted post blocks posting and shows the server time", async () => {
  const p = await page(true, {
    draft: score.EXAMPLE,
    quota: { used: 5, remaining: 0, next_slot_at: "2026-10-01T12:00:00Z" },
  });
  assert.equal(p.$("intend-post").disabled, true);
  assert.match(p.$("quota-status").textContent, /2026-10-01T12:00:00Z/);
});
test("fourth-post warning stays visible when there is no draft to review", async () => {
  const p = await page(true, {
    quota: { used: 4, remaining: 1, next_slot_at: "2026-10-01T12:00:00Z" },
  });
  assert.match(p.$("quota-status").textContent, /1 post left/);
  for (let node = p.$("quota-status"); node; node = node.parentElement)
    assert.equal(node.hidden, false);
});
test("public identity is rendered as text and scopes remain separate", async () => {
  const p = await page(true, {
    rows: [
      {
        login: "<img src=x onerror=alert(1)>",
        score: score.EXAMPLE,
        updated_at: "2026-09-09T00:00:00Z",
      },
    ],
  });
  assert.equal(p.$("score-rows").querySelector("img"), null);
  assert.match(p.$("score-rows").textContent, /<img/);
  p.fire("including-exec");
  await p.tick();
  assert.ok(
    p.calls.some((c) => c.url === "/api/community/scores?scope=including-exec"),
  );
});
test("keyboard arrows switch tabs; slow file reads cannot overwrite a later paste", async () => {
  const p = await page();
  p.fire("upload-tab", "keydown", { key: "ArrowRight" });
  assert.equal(p.$("preview-tab").getAttribute("aria-selected"), "true");
  p.fire("preview-tab", "keydown", { key: "Home" });
  let finish;
  Object.defineProperty(p.$("score-file"), "files", {
    value: [
      { size: 500, text: () => new Promise((resolve) => (finish = resolve)) },
    ],
  });
  p.fire("score-file", "change");
  p.$("score-json").value = "{";
  p.fire("score-json", "input");
  finish(JSON.stringify(score.EXAMPLE));
  await p.tick();
  assert.equal(p.$("share-actions").hidden, true);
  assert.equal(p.$("score-result").hidden, true);
});
test("editing a community score cancels the previous confirmation", async () => {
  const p = await page(true, { draft: score.EXAMPLE });
  p.fire("intend-post");
  await p.tick();
  const challenge = p.context.challenge;
  p.$("score-json").value = "{";
  p.fire("score-json", "input");
  challenge.callback("stale-token");
  p.fire("post-score");
  assert.equal(p.$("draft-review").hidden, true);
  assert.equal(
    p.calls.some((c) => c.method === "POST"),
    false,
  );
  assert.equal(p.storage.size, 0);
});
test("posting locks score edits until the result arrives", async () => {
  let finish;
  const p = await page(true, {
    draft: score.EXAMPLE,
    postResponse: () => new Promise((resolve) => (finish = resolve)),
  });
  p.fire("intend-post");
  await p.tick();
  p.context.challenge.callback("token");
  p.fire("post-score");
  assert.equal(p.$("score-json").disabled, true);
  assert.equal(p.$("score-file").disabled, true);
  assert.equal(p.$("clear-draft").disabled, true);
  finish({
    ok: true,
    json: async () => ({
      ok: true,
      quota: { used: 1, remaining: 4, next_slot_at: null },
    }),
  });
  await p.tick();
  assert.equal(p.$("score-json").disabled, false);
});
test("a failed post keeps the local score and reuses its request ID on retry", async () => {
  const p = await page(true, {
    draft: score.EXAMPLE,
    postResponse: () => {
      throw new Error("network failed");
    },
  });
  for (let i = 0; i < 2; i++) {
    p.fire("intend-post");
    await p.tick();
    p.context.challenge.callback("token" + i);
    p.fire("post-score");
    await p.tick();
  }
  const requests = p.calls
    .filter((c) => c.method === "POST")
    .map((c) => JSON.parse(c.body));
  assert.equal(requests.length, 2);
  assert.equal(requests[0].request_id, requests[1].request_id);
  assert.equal(p.$("draft-review").hidden, false);
});
test("successful posting bypasses cached board responses on its follow-up read", async () => {
  const p = await page(true, { draft: score.EXAMPLE });
  const initialRead = p.calls.find((call) => call.url.includes("?scope="));
  assert.equal(initialRead.cache, undefined);
  p.fire("intend-post");
  await p.tick();
  p.context.challenge.callback("token");
  p.fire("post-score");
  await p.tick();
  const reads = p.calls.filter((call) => call.url.includes("?scope="));
  assert.equal(reads.length, 2);
  assert.equal(reads[1].cache, "no-store");
});
test("successful deletion bypasses cached board responses on its follow-up read", async () => {
  const p = await page(true, {
    ownScores: [
      {
        login: "tester",
        score: score.EXAMPLE,
        updated_at: "2026-09-09T00:00:00Z",
      },
    ],
  });
  p.$("own-scores").querySelector("button").click();
  await p.tick();
  assert.equal(p.calls.filter((call) => call.method === "DELETE").length, 1);
  const reads = p.calls.filter((call) => call.url.includes("?scope="));
  assert.equal(reads.length, 2);
  assert.equal(reads[1].cache, "no-store");
});
