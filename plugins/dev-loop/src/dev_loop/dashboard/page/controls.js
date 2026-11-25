/** Empty clears the cap (null); text that is no number goes as it is, for the server to refuse. */
export function amount(text, by = 1) {
  const t = text.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? Number((n * by).toFixed(6)) : t;
}

/** An agent's context cap, typed in k tokens. */
export const rowBody = (action, id, text) => ({ action, scope: "row", id, value: amount(text, 1000) });
export const pauseBody = (id, paused) => ({ action: paused ? "resume" : "pause", id });
