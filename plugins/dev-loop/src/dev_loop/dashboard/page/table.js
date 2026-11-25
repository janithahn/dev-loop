import { errorText, judge } from "./health.js";
import { ago, h, k, usd } from "./html.js";

const level = (used, cap) => (cap && used >= cap ? "over" : cap && used >= cap * 0.8 ? "hot" : "");
const label = (text) => String(text).replaceAll("_", " ");
const chip = (value, text) => (value == null ? "" : h`<span class="chip">${text(value)}</span>`);

/** The two budgets' role and budget state: shown when present, nothing when absent. */
const tags = (x) => h`${chip(x.role, label)}${chip(x.budget_state, label)}`;

const who = (x) =>
  x.agent_id
    ? h`${x.description || "(agent)"}<div class="muted mono">${x.agent_type ?? ""} · ${x.agent_id.slice(0, 10)}</div>`
    : h`<b>${x.who || "(untitled)"}</b><div class="muted mono">${x.session_id.slice(0, 8)}</div>`;

const input = (x, own, applied) =>
  h`<input class="num" data-act="context" data-id="${x.id}" inputmode="decimal" placeholder="${applied ?? "none"}" value="${own ?? ""}" title="context cap, k tokens">`;

/** The applied cap and how it came about: stated (clamped or not) or the role's default. */
function capNote(x, windows, limits) {
  const c = x.caps;
  if (!c || c.context == null) return "";
  const trip = limits.handoff_threshold ? ` · hand-off at ${k(c.context * limits.handoff_threshold)}` : "";
  const how = c.stated != null ? `stated ${k(c.stated)}${c.clamped ? " · clamped" : ""}` : `default ${k(c.default)}`;
  const wrap = windows[x.id] ? ` · wrap-up: ${windows[x.id]}/10` : "";
  return h`<div class="muted small">${how}${trip}${wrap}</div>`;
}

const pauseButton = (x, paused, inherited) =>
  inherited
    ? h`<button class="btn on" disabled title="Resume the session to let this agent run">Paused with its session</button>`
    : h`<button class="btn ${paused ? "on" : ""}" data-pause="${x.id}">${paused ? "Paused · resume" : "Pause"}</button>`;

/** "Running long" past the configured hours (warnings.running_hours); needs the row's start. */
const longRun = (x, s, limits) =>
  x.status === "running" && x.started && limits.running_hours && s.now - x.started > limits.running_hours * 3600
    ? h`<span class="chip st long">running long</span>`
    : "";

function row(x, s, limits) {
  const c = s.control, kind = x.agent_id ? "agent" : "session";
  const limit = x.caps?.context ?? c.context_caps[x.id], own = c.context_caps[x.id];
  const paused = c.paused.includes(x.id);
  const inherited = Boolean(x.agent_id) && c.paused.includes(x.session_id); // a paused session pauses its agents
  const marker = limit ? h`<em style="left:${Math.min(100, (limit / s.window) * 100)}%"></em>` : "";
  return h`<tr class="${kind}">
    <td>${who(x)}${tags(x)}</td>
    <td><div class="cell"><span class="dot ${x.status}"></span>${x.status}${longRun(x, s, limits)}</div><div class="muted small">last write ${ago(x.written, s.now)}</div></td>
    <td><div class="cell"><div class="gauge"><span class="mono">${k(x.context)}</span><span class="muted"> / ${k(s.window)}</span><div class="track"><i class="${level(x.context, limit)}" style="width:${Math.min(100, (x.context / s.window) * 100)}%"></i>${marker}</div></div>${input(x, own && own / 1000, limit && limit / 1000)}</div>${capNote(x, s.windows, limits)}</td>
    <td class="r"><b>${usd(x.usd)}</b></td>
    <td>${pauseButton(x, paused, inherited)}</td>
  </tr>`;
}

/** Each session followed by its agents; an agent whose session is not listed still shows. */
export function order(rows) {
  const groups = new Map();
  for (const r of rows) groups.set(r.session_id, [...(groups.get(r.session_id) ?? []), r]);
  return [...groups.values()].flatMap((g) => [...g.filter((r) => !r.agent_id), ...g.filter((r) => r.agent_id)]);
}

const hookLine = (s) => {
  const wired = s.hook_wired === null ? "unknown" : s.hook_wired ? "yes" : "no";
  const last = s.hook_errors.last.slice(-3).map((e) => `${ago(e.at, s.now)} ${errorText(e)}`).join("; ");
  const settings = s.settings_error ? ` · settings: ${s.settings_error}` : "";
  return `hook wired: ${wired} · hook errors: ${s.hook_errors.count}${last ? ` (${last})` : ""}${settings}`;
};

/** Everything the page paints for one answer; it throws on a state it cannot read. */
export function present(answer, limits = {}) {
  const verdict = judge(answer);
  if (answer.kind !== "state") return { ...verdict, sub: h`${verdict.title}`, rows: null };
  const s = answer.state;
  const sub = h`${s.sessions.length} rows · today ${usd(s.today_usd)} · burn ${usd(s.burn_usd_h)}/h · API-equivalent, not a bill`;
  return { ...verdict, sub, rows: h`${order(s.sessions).map((x) => row(x, s, limits))}`, hook: hookLine(s), control: s.control };
}
