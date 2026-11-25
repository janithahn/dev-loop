// Self-check for the dev-loop dashboard page's logic: `node --test`.
// The page's modules are pure (no DOM), so each answer, control and request is checked here.
import assert from "node:assert/strict";
import test from "node:test";
import { load, poller, post, restart } from "./dev_loop/dashboard/page/api.js";
import { amount, pauseBody, rowBody } from "./dev_loop/dashboard/page/controls.js";
import { judge } from "./dev_loop/dashboard/page/health.js";
import { h, usd } from "./dev_loop/dashboard/page/html.js";
import { parse, saveBody, settingsView } from "./dev_loop/dashboard/page/settings.js";
import { present } from "./dev_loop/dashboard/page/table.js";

const NOW = 1_800_000_000;
const CONTROL = { paused: [], context_caps: {} };
const ROW = { id: "s1", session_id: "s1", agent_id: null, who: "Fix the page", status: "running", usd: 1.5, context: 250_000, written: NOW - 120, agent_type: null, description: null };
const state = (over = {}) => ({
  now: NOW, scanned_at: NOW - 2, scan_age: 2, scan_error: null, stale: false, pauses_only: false,
  today_usd: 3.2, burn_usd_h: 0.5, dropped: 0, sessions: [ROW], hook_errors: { count: 0, last: [] }, windows: {}, config_error: null,
  threads: { scanner: true }, window: 1_000_000, control: CONTROL, hook_wired: true, settings_error: null, ...over,
});
const answer = (over) => ({ kind: "state", state: state(over) });
const REASONS = {
  "no completed pass": [{ scan_age: null, scanned_at: 0, stale: true }, /no scan has finished/],
  "stale": [{ scan_age: 190, stale: true }, /stale.*3 min/],
  "scan error": [{ scan_error: "PermissionError: denied" }, /scan error: PermissionError: denied/],
  "pauses only": [{ pauses_only: true }, /pauses only/],
  "dead thread": [{ threads: { scanner: true, accounts: false } }, /accounts reader has stopped/],
  "hook errors": [{ hook_errors: { count: 2, last: [{ at: NOW - 5, error: "KeyError", frame: "gate.py:9" }] } }, /2.*KeyError.*gate\.py:9/],
  "config error": [{ config_error: "dev-loop.yaml does not parse" }, /config.*does not parse/],
  "settings error": [{ settings_error: "settings.json does not parse" }, /does not parse/],
};
const reply = (status, body) => async () => ({ ok: status < 400, status, json: async () => body });
const text = (html) => String(html);

test("each answer renders its own message (C-1)", () => {
  const down = judge({ kind: "unreachable", why: "timed out" });
  assert.deepEqual([down.dot, down.reasons], ["red", ["not reachable (timed out)"]]);
  const broke = judge({ kind: "error", reason: "OSError: disk" });
  assert.deepEqual(broke.reasons, ["server error: OSError: disk"]);
  assert.deepEqual(judge({ kind: "page", why: "TypeError: x" }).reasons, ["page error: TypeError: x"]);
  for (const [over, pattern] of Object.values(REASONS)) {
    assert.match(judge(answer(over)).reasons.join("|"), pattern);
  }
});

test("each reason alone turns the dot red and is listed (C-4)", () => {
  for (const [name, [over]] of Object.entries(REASONS)) {
    const verdict = judge(answer(over));
    assert.equal(verdict.dot, "red", name);
    assert.equal(verdict.reasons.length, 1, name);
    assert.equal(present(answer(over)).reasons[0], verdict.reasons[0], name);
  }
  const both = judge(answer({ scan_error: "x", pauses_only: true }));
  assert.equal(both.reasons.length, 2); // every reason is listed, not the first
});

test("the dot is grey before an answer and green only when healthy (C-6)", () => {
  for (const none of [undefined, null, {}, { kind: "none" }]) {
    assert.equal(judge(none).dot, "grey");
    assert.equal(judge(none).title, "waiting for the first answer");
  }
  assert.equal(judge(answer()).dot, "green");
  assert.deepEqual(judge(answer()).reasons, []);
});

test("Refresh fetches once, and one request is in flight at a time (C-3, C-11)", async () => {
  const seen = [];
  let calls = 0;
  const fetchOnce = async (url) => {
    calls += 1;
    await new Promise((resolve) => setTimeout(resolve, 20));
    return reply(200, state())(url);
  };
  const refresh = poller(fetchOnce, (a) => seen.push(a.kind));
  await Promise.all([refresh(""), refresh("")]);
  assert.deepEqual([calls, seen], [1, ["state"]]);
  await refresh("");
  assert.equal(calls, 2);
});

test("Restart posts only after confirmation, with X-DL (C-3)", async () => {
  const sent = [];
  const fetchFn = async (url, init) => {
    sent.push([url, init.method, init.headers["X-DL"]]);
    return reply(200, {})();
  };
  assert.equal(await restart(fetchFn, () => false), null);
  assert.deepEqual(sent, []);
  assert.equal(await restart(fetchFn, () => true), null);
  assert.deepEqual(sent, [["/api/restart", "POST", "1"]]);
});

test("a refused Restart or control shows the server's reason", async () => {
  assert.equal(await restart(reply(500, { error: "OSError: disk" }), () => true), "OSError: disk");
  assert.equal(await post(reply(400, { error: "ValueError: a cap is a finite number" }), {}), "ValueError: a cap is a finite number");
  assert.equal(await post(reply(403, null), {}), "failed (HTTP 403)");
  const refused = async () => {
    throw new TypeError("fetch failed");
  };
  assert.match(await post(refused, {}), /not reachable.*fetch failed/);
});

test("every dollar figure carries the label (C-5)", () => {
  const view = present(answer({ sessions: [{ ...ROW, usd: 12.3456 }], }));
  for (const html of [text(view.sub), text(view.rows)]) {
    const bare = html.replace(/<span class="usd" title="API-equivalent, not a bill">[^<]*<\/span>/g, "");
    assert.doesNotMatch(bare, /\$\d/, html);
  }
  assert.match(text(view.sub), /\$3\.20.*not a bill/);
  assert.match(text(view.rows), /\$12\.35/);
  assert.equal(text(usd(3.2)), '<span class="usd" title="API-equivalent, not a bill">$3.20</span>');
  assert.equal(text(usd(null)), '<span class="usd" title="API-equivalent, not a bill">–</span>');
});

test("a session title is escaped (C-8)", () => {
  const title = "<img src=x onerror=alert(1)>";
  const agent = { ...ROW, id: "a1", agent_id: "a1", description: `"><script>x</script>` };
  const view = present(answer({ sessions: [{ ...ROW, who: title }, agent] }));
  assert.doesNotMatch(text(view.rows), /<img|<script/);
  assert.match(text(view.rows), /&#60;img src=x onerror=alert\(1\)&#62;/);
  assert.equal(text(h`${h`<b>${title}</b>`}`), "<b>&#60;img src=x onerror=alert(1)&#62;</b>"); // markup nests, values do not
  assert.equal(text(h`${null}${undefined}${[1, "<"]}`), "1&#60;");
});

test("the two-budgets fields render when present and nothing when absent (C-10)", () => {
  const plain = text(present(answer()).rows);
  assert.doesNotMatch(plain, /chip|undefined|null/);
  const extra = { role: "builder", budget_state: "handing_off", caps: { context: 400_000, stated: null, default: 400_000, clamped: false } };
  const full = text(present(answer({ sessions: [{ ...ROW, ...extra }] })).rows);
  for (const shownText of ["builder", "handing off", "400k"]) assert.match(full, new RegExp(shownText));
  assert.doesNotMatch(full, /undefined|null|handing_off/);
  const some = text(present(answer({ sessions: [{ ...ROW, role: "reviewer", caps: null }] })).rows);
  assert.equal((some.match(/class="chip"/g) ?? []).length, 1);
});

test("a hung fetch times out as not reachable; a state with no sessions is a page error (C-11)", async () => {
  const hung = (url, { signal }) =>
    new Promise((resolve, reject) => {
      const keep = setTimeout(() => {}, 5000); // a ref'd timer: node's own timeout timer is not
      signal.addEventListener("abort", () => {
        clearTimeout(keep);
        reject(signal.reason);
      });
    });
  const timedOut = await load(hung, "", 20);
  assert.deepEqual(timedOut, { kind: "unreachable", why: "timed out" });
  assert.deepEqual(judge(timedOut).reasons, ["not reachable (timed out)"]);
  const shownAnswers = [];
  const show = (a) => {
    if (a.kind === "state") present(a);
    shownAnswers.push(a);
  };
  await poller(reply(200, { ...state(), sessions: undefined }), show)("");
  const last = shownAnswers.at(-1);
  assert.equal(last.kind, "page");
  assert.equal(judge(last).dot, "red");
  assert.match(judge(last).reasons[0], /^page error: /);
  const server = await load(reply(500, { error: "KeyError: x" }), "");
  assert.deepEqual(server, { kind: "error", reason: "KeyError: x" });
});

test("a state missing a field C-4 reads is a red page error, never green (C-4, C-11)", async () => {
  const keys = ["scan_age", "stale", "pauses_only", "scan_error", "settings_error", "sessions", "threads", "hook_errors"];
  const broken = [
    ...keys.map((key) => [`no ${key}`, { ...state(), [key]: undefined }]),
    ["no scanner thread", state({ threads: {} })],
    ["stale as text", state({ stale: "no" })],
    ["hook errors without count", state({ hook_errors: { last: [] } })],
  ];
  for (const [name, body] of broken) {
    const shown = [];
    await poller(reply(200, JSON.parse(JSON.stringify(body))), (a) => shown.push(judge(a)))("");
    assert.equal(shown.at(-1).dot, "red", name);
    assert.match(shown.at(-1).reasons[0], /^page error: /, name);
  }
});

test("controls send only pause, resume and context (D11)", () => {
  assert.deepEqual(rowBody("context", "s1", "400"), { action: "context", scope: "row", id: "s1", value: 400_000 });
  assert.deepEqual(rowBody("context", "a1", " "), { action: "context", scope: "row", id: "a1", value: null });
  assert.deepEqual(pauseBody("s1", false), { action: "pause", id: "s1" });
  assert.deepEqual(pauseBody("s1", true), { action: "resume", id: "s1" });
  assert.equal(amount("1.1", 1000), 1100); // no float dust: a context cap is whole tokens
  assert.equal(amount("abc"), "abc"); // never NaN, which JSON would turn into null (clear the cap)
  const html = text(present(answer({ sessions: [ROW, { ...ROW, id: "a1", agent_id: "a1" }] })).rows);
  assert.deepEqual([...new Set([...html.matchAll(/data-act="(\w+)"/g)].map((m) => m[1]))], ["context"]);
  assert.doesNotMatch(html, /budget/);
});

test("the context cell shows stated, clamped and default", () => {
  const agent = (caps) => ({ ...ROW, id: "a1", agent_id: "a1", caps });
  const cell = (caps, windows = {}) => text(present(answer({ sessions: [agent(caps)], windows })).rows);
  const stated = cell({ context: 300_000, stated: 300_000, default: 400_000, clamped: false });
  assert.match(stated, /stated 300k/);
  assert.doesNotMatch(stated, /clamped|default/);
  const clamped = cell({ context: 400_000, stated: 900_000, default: 400_000, clamped: true });
  assert.match(clamped, /stated 900k/);
  assert.match(clamped, /clamped/);
  const dflt = cell({ context: 400_000, stated: null, default: 400_000, clamped: false });
  assert.match(dflt, /default 400k/);
  assert.doesNotMatch(dflt, /stated|clamped|wrap-up/);
  assert.doesNotMatch(text(present(answer()).rows), /stated|clamped|default/); // a session: no cap
  assert.match(cell({ context: 400_000, stated: null, default: 400_000, clamped: false }, { a1: 3 }), /wrap-up: 3\/10/);
});

test("health shows hook wired and hook errors", () => {
  const line = (over) => text(present(answer(over)).hook);
  assert.match(line({ hook_wired: true }), /hook wired: yes/);
  assert.match(line({ hook_wired: false }), /hook wired: no/);
  assert.match(line({ hook_wired: null, settings_error: "does not parse" }), /does not parse/);
  const last = [{ at: NOW - 5, error: "KeyError", frame: "gate.py:9" }];
  assert.match(line({ hook_errors: { count: 7, last } }), /hook errors: 7.*KeyError.*gate\.py:9/);
  assert.match(line({}), /hook errors: 0/);
});

test("a row's pause comes from the controls", () => {
  const html = text(present(answer({ control: { ...CONTROL, paused: ["s1"] } })).rows);
  assert.match(html, /Paused · resume/);
});

test("a state missing a field the table reads is a red page error, never NaN under green", async () => {
  const top = ["now", "today_usd", "burn_usd_h", "window", "hook_wired", "control", "windows", "config_error"];
  const rowKeys = ["id", "session_id", "status", "usd", "context", "written", "who", "agent_id"];
  const broken = [
    ...top.map((key) => [`no ${key}`, { ...state(), [key]: undefined }]),
    ...rowKeys.map((key) => [`row without ${key}`, state({ sessions: [{ ...ROW, [key]: undefined }] })]),
    ["usd as text", state({ sessions: [{ ...ROW, usd: "1.5" }] })],
    ["control without paused", state({ control: { ...CONTROL, paused: undefined } })],
    ["control without context_caps", state({ control: { ...CONTROL, context_caps: undefined } })],
  ];
  for (const [name, body] of broken) {
    const seen = [];
    const show = (a) => {
      if (a.kind === "state") present(a);
      seen.push(judge(a));
    };
    await poller(reply(200, JSON.parse(JSON.stringify(body))), show)("");
    assert.equal(seen.at(-1).dot, "red", name);
    assert.match(seen.at(-1).reasons[0], /^page error: /, name);
  }
  assert.equal(judge(answer({ hook_wired: null })).dot, "green"); // unknown wiring is the settings error's to flag
});

test("an agent under a paused session shows it is paused with the session", () => {
  const agent = { ...ROW, id: "a1", agent_id: "a1", description: "worker" };
  const paused = text(present(answer({ sessions: [ROW, agent], control: { ...CONTROL, paused: ["s1"] } })).rows);
  assert.match(paused, /Paused with its session/);
  assert.equal((paused.match(/data-pause=/g) ?? []).length, 1); // only the session can be resumed
  const free = text(present(answer({ sessions: [ROW, agent] })).rows);
  assert.doesNotMatch(free, /with its session/);
  assert.equal((free.match(/data-pause=/g) ?? []).length, 2);
  const own = text(present(answer({ sessions: [ROW, agent], control: { ...CONTROL, paused: ["a1"] } })).rows);
  assert.match(own, /Paused · resume/); // its own pause stays resumable
});

const CONFIG = {
  values: { "handoff.threshold": 0.8, "warnings.running_hours": 4, "roles.coordinator.model": "opus", "caps.enabled": true },
  defaults: { "handoff.threshold": 0.9, "warnings.running_hours": 4, "roles.coordinator.model": "opus", "caps.enabled": true },
  sources: { "handoff.threshold": "project", "warnings.running_hours": "default", "roles.coordinator.model": "default", "caps.enabled": "default" },
  ranges: { "handoff.threshold": [0.5, 1] }, error: null, read_only: ["roles.coordinator.model"],
  drift: [{ role: "builder", agent_type: "builder", file: "sonnet", config: "opus" }],
};

test("settings rows show value, default and source", () => {
  const html = text(settingsView(CONFIG));
  assert.match(html, /handoff\.threshold/);
  assert.match(html, /value="0\.8"/);
  assert.match(html, /default 0\.9/);
  assert.match(html, /project/);
  assert.match(html, /data-reset="handoff\.threshold"/);
  assert.match(html, /data-reset="warnings\.running_hours" disabled/); // at its default: Reset is off
});

test("a read-only model is disabled and drift is shown", () => {
  const html = text(settingsView(CONFIG));
  assert.match(html, /data-key="roles\.coordinator\.model"[^>]*disabled/);
  assert.match(html, /builder.*sonnet.*opus/s);
  assert.match(html, /0\.5.*1/);
});

test("a refused save shows the server's reason, and the page sends one key", async () => {
  assert.deepEqual(saveBody("handoff.threshold", "0.3", 0.9), { key: "handoff.threshold", value: 0.3 });
  assert.deepEqual(saveBody("handoff.threshold", null, 0.9), { key: "handoff.threshold", value: null });
  assert.equal(parse("abc", 4), "abc"); // the server refuses it, the page does not
  const why = await post(reply(400, { error: "ValueError: handoff.threshold: 0.3 is not in 0.5-1" }), saveBody("handoff.threshold", "0.3", 0.9), "/api/config");
  assert.equal(why, "ValueError: handoff.threshold: 0.3 is not in 0.5-1");
  assert.match(text(settingsView(CONFIG, { "handoff.threshold": why })), /0\.3 is not in 0\.5-1/);
});

test("running long uses the configured hours, the trip the configured share", () => {
  const long = { ...ROW, started: NOW - 3 * 3600, caps: { context: 200_000, default: 200_000, stated: null, clamped: false } };
  const shown = (hours, threshold) =>
    String(present(answer({ sessions: [long] }), { running_hours: hours, handoff_threshold: threshold }).rows);
  assert.doesNotMatch(shown(4, 0.9), /running long/);
  assert.match(shown(2, 0.9), /running long/);
  assert.match(shown(4, 0.8), /hand-off at 160k/);
});
