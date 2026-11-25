const HEADERS = { "Content-Type": "application/json", "X-DL": "1" };
const why = (e) => (e instanceof Error && e.name === "TimeoutError" ? "timed out" : e instanceof Error ? e.message : String(e));

/** GET the state, cut at `ms`: an answer for health.judge, never a throw. */
export async function load(fetchFn, query, ms = 10_000) {
  try {
    const r = await fetchFn(`/api/state${query}`, { signal: AbortSignal.timeout(ms) });
    const body = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    return r.ok ? { kind: "state", state: body } : { kind: "error", reason: body.error ?? `HTTP ${r.status}` };
  } catch (e) {
    return { kind: "unreachable", why: why(e) };
  }
}

/** One request in flight at a time; a render that throws is shown as a page error. */
export function poller(fetchFn, show) {
  let busy = false;
  return async (query) => {
    if (busy) return;
    busy = true;
    try {
      const answer = await load(fetchFn, query);
      try {
        show(answer);
      } catch (e) {
        show({ kind: "page", why: why(e) });
      }
    } finally {
      busy = false;
    }
  };
}

/** POST one change; return null, or the server's reason when it refused (B-5). */
export async function post(fetchFn, body, url = "/api/control") {
  try {
    const r = await fetchFn(url, { method: "POST", headers: HEADERS, body: JSON.stringify(body), signal: AbortSignal.timeout(10_000) });
    return r.ok ? null : ((await r.json().catch(() => null))?.error ?? `failed (HTTP ${r.status})`);
  } catch (e) {
    return `not reachable (${why(e)})`;
  }
}

export const restart = async (fetchFn, confirmFn) =>
  confirmFn("Restart the dashboard's readers? It re-reads every transcript. Your caps and pauses are kept.")
    ? post(fetchFn, {}, "/api/restart")
    : null;
