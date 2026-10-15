import { cancel } from "../actions.js";
import { api } from "../api.js";
import { go } from "../app.js";
import { MONEY, STATUS, add, button, chip, dayLabel, h, list, rupees, sheet, topbar, when } from "../ui.js";
import { row } from "./home.js";
import { report } from "./result.js";

const FILTERS = [["all", "All"], ["money", "Money"], ["account", "Account changes"]];
const PENDING = new Set(["AWAITING_VERIFICATION", "UNDER_REVIEW"]);

export async function render(root, [openId]) {
  let filter = "all";
  let activity = (await api("GET", "/api/activity")).activity;
  const body = h("div", { class: "stack" });
  const chips = h("div", { class: "segmented", role: "group", "aria-label": "Show" });
  const page = h("div", { class: "page with-tabs" }, topbar({ title: "Activity" }), chips, body);
  root.appendChild(page);

  function paint() {
    chips.replaceChildren(...FILTERS.map(([key, label]) => h("button", { type: "button", class: key === filter ? "on" : "",
      "aria-pressed": key === filter ? "true" : "false", onclick: () => { filter = key; paint(); }, text: label })));
    const shown = activity.filter((a) => filter === "all" || (filter === "money") === MONEY.has(a.kind));
    if (!shown.length) {
      body.replaceChildren(h("div", { class: "card empty" }, h("strong", { text: "Nothing here yet" }),
        h("span", { text: "Payments and account changes will appear here." })));
      return;
    }
    const groups = new Map();
    for (const a of shown) {
      const d = dayLabel(a.createdAt);
      if (!groups.has(d)) groups.set(d, []);
      groups.get(d).push(a);
    }
    body.replaceChildren(...[...groups].flatMap(([day, items]) => [h("div", { class: "section-label", text: day }), list(items.map(row))]));
  }
  paint();

  const poll = setInterval(async () => {   // follow anything still pending, e.g. a review an operator is deciding
    if (!page.isConnected) return clearInterval(poll);
    if (!activity.some((a) => PENDING.has(a.status))) return;
    activity = (await api("GET", "/api/activity").catch(() => ({ activity }))).activity;
    paint();
  }, 5000);

  const target = openId && activity.find((a) => a.id === openId);
  if (target) detail(target);
}

function detail(a) {
  const money = MONEY.has(a.kind);
  const [label, tone] = STATUS[a.status] || [a.status, ""];
  sheet((box, close) => {
    add(box,
      h("div", { class: "row-between" }, h("h2", { text: money ? rupees(a.amountMinor) : "Account change" }), chip(label, tone)),
      h("p", { class: "lede", text: a.title }),
      h("div", { class: "card tight", style: { background: "var(--bg)" } },
        h("div", { class: "kv" }, h("span", { text: "When" }), h("span", { text: when(a.createdAt) })),
        h("div", { class: "kv" }, h("span", { text: "Reference" }), h("span", { text: a.id })),
        a.confirmedWith.length ? h("div", { class: "kv" }, h("span", { text: "Confirmed with" }), h("span", { text: a.confirmedWith.join(", ") })) : null,
        a.detail ? h("div", { class: "kv" }, h("span", { text: "Note" }), h("span", { text: a.detail })) : null),
      a.canContinue ? button("Continue", { onClick: () => { close(); go(`verify/${a.id}`); } }) : null,
      PENDING.has(a.status) ? button(money ? "Cancel payment" : "Cancel request", { kind: "danger", onClick: async () => {
        await cancel(a.id);
        close();
        go("activity");
      } }) : null,
      a.status === "DONE" && money && !a.reported ? button("This wasn't me", { kind: "danger", onClick: () => { close(); report(a, () => go("activity")); } }) : null,
      a.reported ? h("p", { class: "muted center", text: "Reported to our fraud team." }) : null,
      button("Close", { kind: "secondary", onClick: close }));
  }, () => { if (location.hash.startsWith(`#/activity/${a.id}`)) history.replaceState(null, "", "#/activity"); });
}
