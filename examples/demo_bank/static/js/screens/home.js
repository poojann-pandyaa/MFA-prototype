import { api } from "../api.js";
import { go, state } from "../app.js";
import { MONEY, STATUS, avatar, button, chip, h, icon, item, list, rupeesParts, rupees, shortName, tile, when } from "../ui.js";

function greeting() {
  const hr = new Date().getHours();
  return hr < 12 ? "Good morning" : hr < 17 ? "Good afternoon" : "Good evening";
}

function hidden() {
  try { return sessionStorage.getItem("bank.hideBalance") === "1"; } catch { return false; }
}

export async function render(root) {
  const me = state.me;
  const { activity } = await api("GET", "/api/activity");
  const first = me.name.split(" ")[0];

  const amount = h("div", { class: "amount" });
  const eye = h("button", { type: "button", class: "ghost" });
  function paintBalance() {
    const hide = hidden();
    const [whole, paise] = rupeesParts(me.balance);
    amount.replaceChildren(hide ? "₹ • • • • •" : "₹" + whole, hide ? "" : h("small", { text: paise }));
    eye.replaceChildren(icon(hide ? "eye" : "eyeOff", 18));
    eye.setAttribute("aria-label", hide ? "Show balance" : "Hide balance");
  }
  eye.addEventListener("click", () => {
    try { sessionStorage.setItem("bank.hideBalance", hidden() ? "0" : "1"); } catch { /* private mode */ }
    paintBalance();
  });
  paintBalance();
  const used = Math.min(100, Math.round(100 * me.sentToday / Math.max(me.dailyLimit, 1)));

  const people = (me.recentPayees.length ? me.recentPayees : me.payees).slice(0, 5);
  const p = me.protection;
  const recent = activity.slice(0, 3);

  root.appendChild(h("div", { class: "page with-tabs" },
    h("div", { class: "row-between" },
      h("div", {}, h("div", { class: "muted", text: greeting() }), h("h2", { text: first })),
      h("button", { type: "button", class: "icon-btn", "aria-label": "Profile and security", onclick: () => go("security") },
        avatar(me.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase(), "sm"))),

    h("section", { class: "balance-card", "aria-label": "Balance" },
      h("div", { class: "row-between" }, h("span", { class: "small", text: `Savings · ${me.ref}` }), eye),
      amount,
      h("div", { class: "meter", "aria-hidden": "true" }, h("span", { style: { width: used + "%" } })),
      h("p", { class: "small", style: { marginTop: "8px", opacity: "0.9" },
        text: `${rupees(me.sentToday)} of ${rupees(me.dailyLimit)} daily limit used today` })),

    h("div", { class: "quick" },
      quick("send", "Send", () => go("pay")),
      quick("bill", "Pay bills", () => go("pay/bills")),
      quick("payee", "Add payee", () => go("newpayee")),
      quick("shield", "Security", () => go("security"))),

    h("section", { class: "stack" },
      h("h3", { text: "Pay again" }),
      h("div", { class: "people" },
        people.map((x) => h("button", { type: "button", onclick: () => go(`amount/transfer/${x.ref}`), "aria-label": `Pay ${x.display}` },
          avatar(x.initials), h("span", { text: shortName(x.display) }))),
        h("button", { type: "button", onclick: () => go("newpayee") }, h("span", { class: "new" }, icon("plus", 20)), h("span", { text: "New" })))),

    p.level === "Strong" ? null : h("div", { class: "card" }, h("div", { class: "row-between" },
      tile("shieldAlert", "warn"),
      h("div", { style: { flex: "1" } }, h("h3", { text: `Protection: ${p.level}` }),
        h("p", { class: "muted small", text: p.canAdd.length ? `Add your ${p.canAdd[0].toLowerCase()} to make it stronger` : "" })),
      button("Add", { kind: "soft", onClick: () => go("security") }))),

    h("section", { class: "stack" },
      h("div", { class: "row-between" }, h("h3", { text: "Recent" }), h("a", { class: "link small", href: "#/activity", text: "See all" })),
      recent.length ? list(recent.map(row)) : h("div", { class: "card muted center", text: "Your payments will show up here." }))));
}

function quick(ic, label, onClick) {
  return h("button", { type: "button", onclick: onClick }, h("span", { class: "tile" }, icon(ic, 24)), label);
}

export function row(a) {
  const [label, tone] = STATUS[a.status] || [a.status, ""];
  const money = MONEY.has(a.kind);
  const sub = a.status === "DONE" ? when(a.createdAt) : h("span", { class: "sub" }, chip(label, tone));
  return item({
    lead: money && a.kind === "transfer" ? avatar(a.payee.initials) : tile(a.kind === "bill" ? "bolt" : a.kind === "device" ? "phone" : "shield", "grey"),
    title: a.title, sub,
    trail: money ? (a.status === "DONE" ? "−" : "") + rupees(a.amountMinor) : null,
    trailClass: money && a.status !== "DONE" && a.status !== "UNDER_REVIEW" && a.status !== "AWAITING_VERIFICATION" ? "struck" : "",
    onClick: () => go(`activity/${a.id}`),
  });
}
