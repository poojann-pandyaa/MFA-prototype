import { cancel, find } from "../actions.js";
import { go } from "../app.js";
import { MONEY, avatar, button, chip, h, icon, note, rupees, tile, topbar } from "../ui.js";

// What the customer is about to approve, which checks are coming and why, and a scam warning when AMFA asks.
export const HEADINGS = {
  transfer: "Review payment", bill: "Review bill payment", payee: "Add a new payee", limit: "Change your daily limit",
  contact: "Change your contact details", password: "Change your password", device: "Verify this device",
};

export async function render(root, [id]) {
  const { action: a } = await find(id);
  if (a.status !== "AWAITING_VERIFICATION") return go(`result/${id}`);
  const money = MONEY.has(a.kind);
  const back = async () => { await cancel(id).catch(() => null); history.back(); };

  const summary = money
    ? h("div", { class: "card summary" },
      avatar(a.payee.initials, "lg"),
      h("span", { class: "muted", text: a.kind === "bill" ? "You're paying" : "You're sending" }),
      h("span", { class: "amount", text: rupees(a.amountMinor) }),
      h("strong", { text: `to ${a.payee.display}` }),
      h("div", { class: "divider" }),
      h("div", { style: { width: "100%" } },
        h("div", { class: "kv" }, h("span", { text: "To" }), h("span", { text: a.payee.display })),
        h("div", { class: "kv" }, h("span", { text: "From" }), h("span", { text: "Savings account" })),
        h("div", { class: "kv" }, h("span", { text: "Type" }), h("span", { text: a.kind === "bill" ? "Bill payment" : "IMPS transfer" }))))
    : h("div", { class: "card summary" }, tile(a.kind === "device" ? "phone" : a.kind === "payee" ? "payee" : "shield"),
      h("strong", { text: a.title.replace(/^(Added|Changed|Verified)/, (w) => ({ Added: "Add", Changed: "Change", Verified: "Verify" })[w]) }));

  const n = a.checks.length;
  root.appendChild(h("div", { class: "page" },
    topbar({ title: HEADINGS[a.kind], back }),
    summary,
    h("div", { class: "card stack" },
      h("div", { class: "row-between", style: { justifyContent: "flex-start" } }, tile("shieldCheck"),
        h("strong", { text: n ? `We'll confirm it's you in ${n} quick ${n === 1 ? "step" : "steps"}` : "We'll confirm it's you" })),
      h("div", { class: "chips" }, a.checks.map((c, i) => chip(`${i + 1} · ${c.charAt(0).toUpperCase() + c.slice(1)}`))),
      a.reason ? h("p", { class: "muted", text: a.reason }) : null),
    a.reviewAfter ? note("brand", "shieldClock", h("strong", { text: "Then a quick check by our team" }),
      "After these steps, someone at Demo Bank will look at this before it goes through. Your money stays in your account until then.") : null,
    a.scamWarning ? note("warn", "warn", h("strong", { text: "Did someone ask you to make this payment?" }),
      "Demo Bank will never ask you to move money to keep it safe, to share a code, or to pay to receive a refund or prize.") : null,
    h("div", { class: "spacer" }),
    button(money ? `Confirm and pay ${rupees(a.amountMinor)}` : "Confirm", { onClick: () => go(`verify/${id}`) }),
    h("button", { type: "button", class: "link center", text: "Cancel", onclick: back })));
}
