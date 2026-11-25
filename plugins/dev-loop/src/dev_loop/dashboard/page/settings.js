import { h } from "./html.js";

/** Text typed for a key, back to the value the server expects; the server is the one validator. */
export function parse(text, def) {
  if (typeof def === "boolean") return text === "true";
  if (typeof def === "number") return text.trim() === "" || Number.isNaN(Number(text)) ? text : Number(text);
  if (def !== null && typeof def === "object") {
    try { return JSON.parse(text); } catch { return text; }
  }
  return text.trim() === "" ? null : text;
}

/** The one-key body for POST /api/config; text null is a Reset (the server returns it to its default). */
export const saveBody = (key, text, def) => ({ key, value: text === null ? null : parse(text, def) });

const shown = (v) => (v !== null && typeof v === "object" ? JSON.stringify(v) : String(v ?? ""));

function field(key, config) {
  const value = config.values[key];
  const off = config.read_only.includes(key) ? h` disabled title="the role has no agent file in this repository"` : h``;
  return typeof value === "boolean"
    ? h`<input type="checkbox" data-key="${key}" aria-label="${key}"${value ? h` checked` : h``}${off}>`
    : h`<input class="num wide" data-key="${key}" aria-label="${key}" value="${shown(value)}"${off}>`;
}

function row(key, config, errors) {
  const range = config.ranges[key];
  const hint = range ? h` · allowed ${range.join(" to ")}` : h``;
  const reset = config.sources[key] === "project" ? h`` : h` disabled`;
  const refused = errors[key] ? h`<div class="alert" role="alert">${errors[key]}</div>` : h``;
  return h`<tr><td class="mono">${key}</td><td>${field(key, config)}${refused}</td>
    <td class="sub">default ${shown(config.defaults[key])}${hint}</td><td class="sub">${config.sources[key] ?? "default"}</td>
    <td><button class="btn" data-save="${key}">Save</button> <button class="btn" data-reset="${key}"${reset}>Reset</button></td></tr>`;
}

const driftLine = (d) => h`<li>${d.role}: ${d.agent_type}.md says ${d.file ?? "no model"}, the config says ${d.config}</li>`;

/** The Settings body: every key with its value, default, source, Save and Reset; drift above. */
export function settingsView(config, errors = {}) {
  const bad = config.error ? h`<p class="alert" role="alert">${config.error}</p>` : h``;
  const drift = config.drift.length ? h`<p class="alert">Model drift</p><ul>${config.drift.map(driftLine)}</ul>` : h``;
  const keys = Object.keys(config.values).sort();
  return h`${bad}${drift}<table><tbody>${keys.map((key) => row(key, config, errors))}</tbody></table>`;
}
