import { next, start } from "../actions.js";
import { state } from "../app.js";
import { button, field, h, note, rupees, topbar } from "../ui.js";

const PRESETS = [50000, 100000, 200000, 500000];

export function render(root) {
  const me = state.me;
  const input = h("input", { class: "input", type: "number", inputmode: "numeric", min: "1000", max: "1000000", step: "1000",
    value: String(me.dailyLimit / 100) });
  const error = h("p", { class: "form-error", role: "alert" });
  const submit = button("Continue", { type: "submit" });
  const form = h("form", { class: "stack", novalidate: true },
    field({ label: "New daily limit (₹)", id: "lim", input }),
    h("div", { class: "segmented" }, PRESETS.map((v) => h("button", { type: "button", text: rupees(v * 100), onclick: () => { input.value = String(v); } }))),
    note("plain", "info", "Raising your limit needs a quick check. A lower limit makes it harder for anyone to empty your account."),
    error, submit);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const v = Number(input.value);
    if (!Number.isInteger(v) || v < 1000 || v > 1000000) { error.textContent = "Choose a whole amount from ₹1,000 to ₹10,00,000."; return; }
    if (v * 100 === me.dailyLimit) { error.textContent = "That's already your limit."; return; }
    submit.disabled = true;
    error.textContent = "";
    try {
      next(await start("limit", { limitRupees: v }));
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false;
    }
  });
  root.appendChild(h("div", { class: "page" }, topbar({ title: "Daily limit", back: () => history.back() }),
    h("p", { class: "lede", text: `You can send up to ${rupees(me.dailyLimit)} a day now.` }), form));
}
