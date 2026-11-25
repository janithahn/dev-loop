/** Markup that is already safe: only `h` makes one, and `h` escapes every value it is given. */
export class Html {
  constructor(text) {
    this.text = text;
  }

  toString() {
    return this.text;
  }
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const part = (v) => (v instanceof Html ? v.text : Array.isArray(v) ? v.map(part).join("") : esc(v ?? ""));

/** A tagged template: values are escaped text unless they are themselves `h` results. */
export const h = (strings, ...values) => new Html(strings.reduce((out, s, i) => out + part(values[i - 1]) + s));

const dollars = (x) => `$${x.toFixed(x >= 100 ? 0 : x >= 1 ? 2 : 3)}`;
/** Every dollar figure carries its label. */
export const usd = (x) =>
  h`<span class="usd" title="API-equivalent, not a bill">${x == null ? "–" : dollars(x)}</span>`;

export const k = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : String(Math.trunc(n)));

export const ago = (t, now) => {
  if (!t) return "–";
  const s = Math.max(0, now - t);
  const [n, unit] = s < 60 ? [s, "s"] : s < 3600 ? [s / 60, "m"] : s < 86400 ? [s / 3600, "h"] : [s / 86400, "d"];
  return `${Math.floor(n)}${unit} ago`;
};
