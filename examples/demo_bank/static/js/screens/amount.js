import { next, start } from "../actions.js";
import { go, state } from "../app.js";
import { avatar, button, h, icon, keypad, press, rupees, tile } from "../ui.js";

export function render(root, [kind, ref]) {
  const me = state.me;
  const target = kind === "bill" ? me.billers.find((b) => b.ref === ref) : me.payees.find((p) => p.ref === ref);
  if (!target || (kind !== "bill" && kind !== "transfer")) return go("pay");

  let text = "";
  const left = Math.max(0, me.dailyLimit - me.sentToday);
  const shown = h("div", { class: "big-amount", "aria-live": "polite" });
  const hint = h("p", { class: "muted center" });
  const error = h("p", { class: "form-error center", role: "alert" });
  const go_ = button("Continue", { onClick: submit });

  function minor() { return Math.round(parseFloat(text || "0") * 100); }
  function paint() {
    shown.replaceChildren(h("span", { class: "cur", text: "₹" }), text ? Number(text.split(".")[0]).toLocaleString("en-IN") + (text.includes(".") ? "." + text.split(".")[1] : "") : "0");
    const m = minor();
    const over = m > left ? `That's more than today's limit left (${rupees(left)}).` : m > me.balance ? "That's more than your balance." : "";
    hint.textContent = over || `${rupees(left)} of today's limit left`;
    hint.className = over ? "form-error center" : "muted center";
    go_.disabled = !m || !!over;
  }
  async function submit() {
    go_.disabled = true;
    error.textContent = "";
    go_.replaceChildren("Checking…");
    try {
      const body = kind === "bill" ? { billerRef: ref, amountRupees: minor() / 100 } : { payeeRef: ref, amountRupees: minor() / 100 };
      next(await start(kind, body));
    } catch (e) {
      error.textContent = e.message;
      go_.replaceChildren("Continue");
      paint();
    }
  }
  paint();

  const page = h("div", { class: "page", tabindex: "-1" },
    h("div", { class: "topbar" },
      h("button", { type: "button", class: "icon-btn", "aria-label": "Back", onclick: () => history.back() }, icon("back", 20)),
      h("div", { class: "card", style: { flex: "1", padding: "8px 12px", display: "flex", gap: "10px", alignItems: "center", borderRadius: "16px" } },
        kind === "bill" ? tile("bolt") : avatar(target.initials, "sm"),
        h("div", {}, h("strong", { text: target.display }), h("div", { class: "muted small", text: kind === "bill" ? "Bill payment" : "Bank transfer (IMPS)" })))),
    h("div", { class: "spacer" }),
    h("div", { class: "center stack" }, shown, hint),
    h("div", { class: "spacer" }),
    error,
    keypad((k) => { text = press(text, k); paint(); }),
    go_);
  page.addEventListener("keydown", (e) => {   // a hardware keyboard works too
    const k = e.key === "Backspace" ? "del" : /^[0-9.]$/.test(e.key) ? e.key : e.key === "Enter" ? "enter" : null;
    if (!k || e.target.tagName === "BUTTON" && e.key === "Enter") return;
    e.preventDefault();
    if (k === "enter") { if (!go_.disabled) submit(); return; }
    text = press(text, k);
    paint();
  });
  root.appendChild(page);
  page.focus();
}
