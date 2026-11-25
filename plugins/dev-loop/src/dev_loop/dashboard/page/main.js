import { poller, post, restart } from "./api.js";
import { pauseBody, rowBody } from "./controls.js";
import { saveBody, settingsView } from "./settings.js";
import { present } from "./table.js";

const $ = (id) => /** @type {HTMLInputElement} */ (document.getElementById(id));
const from = (e, selector) => /** @type {HTMLInputElement | null} */ (/** @type {Element} */ (e.target).closest(selector));
let editing = false;
let limits = {}; // warnings.running_hours and handoff.threshold, from the config
let config = null;
let errors = {}; // no re-render while an input has focus

function show(answer) {
  const view = present(answer, limits); // throws before anything is painted: the poller shows a page error
  $("live").className = `dot ${view.dot}`;
  $("live").title = view.title;
  $("stale").hidden = !view.reasons.length;
  $("stale").textContent = view.reasons.join(" · ");
  $("sub").innerHTML = String(view.sub);
  if (!view.rows || editing) return;
  $("rows").innerHTML = String(view.rows);
  $("hookStat").textContent = view.hook;
}

async function loadConfig() {
  const r = await fetch("/api/config");
  config = await r.json();
  limits = { running_hours: config.values["warnings.running_hours"], handoff_threshold: config.values["handoff.threshold"] };
  $("cfg").innerHTML = String(settingsView(config, errors));
}
document.addEventListener("click", async (e) => {
  const save = from(e, "button[data-save]"), reset = from(e, "button[data-reset]");
  const key = save?.dataset.save ?? reset?.dataset.reset;
  if (!key) return;
  const input = /** @type {HTMLInputElement} */ (document.querySelector(`input[data-key="${key}"]`));
  const text = input.type === "checkbox" ? String(input.checked) : input.value;
  const why = await post(fetch, saveBody(key, reset ? null : text, config.defaults[key]), "/api/config");
  errors = why ? { [key]: why } : {};
  await loadConfig();
});

const refresh = poller(fetch, show);
const act = async (body) => {
  const why = await post(fetch, body);
  if (why) alert(why);
  refresh("");
  return !why;
};

$("refreshBtn").addEventListener("click", () => refresh(""));
$("restartBtn").addEventListener("click", async () => {
  const why = await restart(fetch, (m) => confirm(m));
  if (why) alert(`Restart failed: ${why}`);
  refresh("");
});
document.addEventListener("click", async (e) => {
  const button = from(e, "button[data-pause]");
  if (button) await act(pauseBody(button.dataset.pause, button.classList.contains("on")));
});
document.addEventListener("change", async (e) => {
  const cell = from(e, "input[data-act]");
  if (cell) await act(rowBody(cell.dataset.act, cell.dataset.id, cell.value));
});
document.addEventListener("focusin", (e) => (editing = Boolean(from(e, ".num"))));
document.addEventListener("focusout", () => (editing = false));
setInterval(() => !editing && refresh(""), 3000);
refresh("");
loadConfig().catch(() => {});
