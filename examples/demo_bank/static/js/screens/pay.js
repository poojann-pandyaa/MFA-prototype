import { go, state } from "../app.js";
import { avatar, h, icon, item, list, topbar } from "../ui.js";

const BILL_ICONS = { "BILL-ELEC": "bolt", "BILL-MOBILE": "phone", "BILL-GAS": "drop" };

export function render(root, [mode]) {
  const me = state.me;
  const recent = new Set(me.recentPayees.map((p) => p.ref));
  const people = h("div", { class: "stack" });
  const bills = h("div", { class: "stack" });
  const search = h("input", { type: "search", placeholder: "Name or biller", "aria-label": "Search payees and billers", autocomplete: "off" });

  function paint() {
    const q = search.value.trim().toLowerCase();
    const ps = me.payees.filter((p) => p.display.toLowerCase().includes(q));
    const bs = me.billers.filter((b) => b.display.toLowerCase().includes(q));
    people.replaceChildren(
      h("div", { class: "section-label", text: "People" }),
      ps.length ? list(ps.map((p) => item({ lead: avatar(p.initials), title: p.display, sub: recent.has(p.ref) ? "Paid recently" : "Saved payee",
        chevron: true, onClick: () => go(`amount/transfer/${p.ref}`) })))
        : h("div", { class: "card muted center", text: q ? "No payee matches that." : "No saved payees yet." }));
    bills.replaceChildren(
      h("div", { class: "section-label", text: "Bills" }),
      bs.length ? h("div", { class: "bills" }, bs.map((b) => h("button", { type: "button", onclick: () => go(`amount/bill/${b.ref}`) },
        icon(BILL_ICONS[b.ref] || "bill", 24), b.display))) : h("div", { class: "card muted center", text: "No biller matches that." }));
  }
  search.addEventListener("input", paint);
  paint();

  const sections = mode === "bills" ? [bills, people] : [people, bills];
  root.appendChild(h("div", { class: "page with-tabs" },
    topbar({ title: mode === "bills" ? "Pay a bill" : "Who are you paying?" }),
    h("label", { class: "search" }, icon("search", 20), search),
    h("button", { type: "button", class: "new-row", onclick: () => go("newpayee") },
      h("span", { class: "tile" }, icon("plus", 20)),
      h("span", { style: { flex: "1" } }, h("strong", { text: "New payee" }), h("span", { class: "sub", text: "Account number and IFSC" })),
      icon("chevron", 18)),
    sections));
}
