const minutes = (seconds) => Math.max(1, Math.round(seconds / 60));
const isBool = (v) => typeof v === "boolean";
const textOrNull = (v) => v === null || typeof v === "string";
const number = Number.isFinite;
const numberOrNull = (v) => v === null || Number.isFinite(v);
const text = (v) => typeof v === "string";
const record = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** Each field it reads, with its type: a missing or renamed field is a page error, never green. */
const SHAPE = {
  scan_age: (v) => v === null || Number.isFinite(v),
  stale: isBool,
  pauses_only: isBool,
  scan_error: textOrNull,
  settings_error: textOrNull,
  sessions: Array.isArray,
  threads: (v) => isBool(v?.scanner),
  hook_errors: (v) => Number.isInteger(v?.count) && Array.isArray(v?.last),
  windows: record,
  config_error: textOrNull,
  // what the table and the sub line read: a missing one is a page error, never NaN
  now: number,
  today_usd: number,
  burn_usd_h: number,
  window: number,
  hook_wired: (v) => v === null || isBool(v),
  control: (v) =>
Array.isArray(v?.paused) && record(v?.context_caps),
};

/** Each field a row paints; role, budget_state and caps are the two budgets' and may be absent. */
const ROW = { id: text, session_id: text, status: text, usd: number, context: number, written: numberOrNull, who: textOrNull, agent_id: textOrNull };

function readable(s) {
  for (const [key, ok] of Object.entries(SHAPE)) if (!ok(s?.[key])) throw new TypeError(`the state's ${key} is missing or unreadable`);
  for (const row of s.sessions) {
    for (const [key, ok] of Object.entries(ROW)) if (!ok(row?.[key])) throw new TypeError(`a session's ${key} is missing or unreadable`);
  }
  return s;
}

export const errorText = (e) => (e ? `${e.error} ${e.frame}` : "unknown");

/** Every reason the state's numbers may be wrong, each from the server's own fields. */
function reasons(s) {
  const out = [];
  if (s.scan_age === null) out.push("no scan has finished yet");
  else if (s.stale) out.push(`data is stale: last scan ${minutes(s.scan_age)} min ago`);
  if (s.scan_error) out.push(`scan error: ${s.scan_error}`);
  if (s.pauses_only) out.push("the gate enforces pauses only: spend data is stale");
  for (const [name, alive] of Object.entries(s.threads)) if (!alive) out.push(`the ${name} reader has stopped: press Restart`);
  if (s.hook_errors.count) out.push(`context hook errors: ${s.hook_errors.count} (calls let through): ${errorText(s.hook_errors.last.at(-1))}`);
  if (s.config_error) out.push(`config: ${s.config_error}`);
  if (s.settings_error) out.push(`settings: ${s.settings_error}`);
  return out;
}

const verdict = (list) => ({ dot: list.length ? "red" : "green", reasons: list, title: list.join(" · ") || "live: data arriving" });

/** The one judgment of the last answer: grey before any, green only when nothing is wrong. */
export function judge(answer) {
  switch (answer?.kind) {
    case "state": return verdict(reasons(readable(answer.state)));
    case "unreachable": return verdict([`not reachable (${answer.why})`]);
    case "error": return verdict([`server error: ${answer.reason}`]);
    case "page": return verdict([`page error: ${answer.why}`]);
    default: return { dot: "grey", reasons: [], title: "waiting for the first answer" };
  }
}
