import { cancel, find } from "../actions.js";
import { api } from "../api.js";
import { go, refresh } from "../app.js";
import { MONEY, add, button, chip, h, icon, rupees, sheet, toast, when } from "../ui.js";

const DONE_TITLE = {
  payee: "Payee added", limit: "Daily limit updated", contact: "Contact details updated",
  password: "Password changed", device: "This device is verified",
};

export async function render(root, [id]) {
  const { action: a } = await find(id);
  const money = MONEY.has(a.kind);
  const page = h("div", { class: "page" });
  root.appendChild(page);
  ({ DONE: done, UNDER_REVIEW: held, AWAITING_VERIFICATION: waiting }[a.status] || notDone)(page, a, money);
  if (a.status === "UNDER_REVIEW") poll(page, id, a.status);
}

function poll(page, id, status) {   // an operator may decide while the customer watches
  const t = setInterval(async () => {
    if (!page.isConnected) return clearInterval(t);
    const { action } = await find(id).catch(() => ({ action: null }));
    if (action && action.status !== status) {
      clearInterval(t);
      await refresh();
      if (page.isConnected) go(`result/${id}`);
    }
  }, 5000);
}

function mark(kind, ic) {
  return kind === "warn" ? h("div", { class: "result-mark warn" }, icon(ic, 48))
    : h("div", { class: `result-mark ${kind}` }, h("div", { class: "dot" }, icon(ic, 40)));
}

function done(page, a, money) {
  add(page, 
    mark("ok", "check"),
    h("div", { class: "center stack", style: { gap: "6px" } },
      h("h1", { text: money ? `${rupees(a.amountMinor)} ${a.kind === "bill" ? "paid" : "sent"}` : DONE_TITLE[a.kind] || "Done" }),
      money ? h("p", { class: "lede", text: `to ${a.payee.display}` }) : h("p", { class: "lede", text: a.title })),
    h("div", { class: "card tight" },
      h("div", { class: "kv" }, h("span", { text: "When" }), h("span", { text: when(a.createdAt) })),
      h("div", { class: "kv" }, h("span", { text: "Reference" }), h("span", { text: a.id })),
      a.confirmedWith.length ? h("div", { class: "kv" }, h("span", { text: "Confirmed with" }),
        h("span", { class: "chips" }, a.confirmedWith.map((c) => chip(c, "brand")))) : null),
    h("div", { class: "spacer" }),
    money ? h("div", { class: "btn-row" }, button("Share receipt", { kind: "secondary", onClick: () => share(a) }),
      button("Done", { onClick: () => go("home") })) : button("Done", { onClick: () => go("home") }),
    money && !a.reported ? h("p", { class: "muted center" }, "Didn't make this payment? ",
      h("button", { type: "button", class: "link bad", text: "Report it", onclick: () => report(a) })) : null,
    a.reported ? h("p", { class: "muted center", text: "Reported to our fraud team. We'll call you shortly." }) : null);
}

function held(page, a, money) {
  const step = (state, title, sub) => h("div", { class: "t" },
    h("div", { class: "rail" }, h("span", { class: `node ${state}` }, state === "done" ? icon("check", 14) : null), h("span", { class: "line" })),
    h("div", { style: { paddingBottom: "8px" } }, h("strong", { text: title }), sub ? h("div", { class: "muted small", text: sub }) : null));
  add(page, 
    mark("warn", "shieldClock"),
    h("div", { class: "center stack", style: { gap: "8px" } },
      h("h1", { text: money ? "We're double-checking this payment" : "We're double-checking this request" }),
      h("p", { class: "lede", text: money ? `${rupees(a.amountMinor)} to ${a.payee.display} is on hold while our team takes a quick look. Your money hasn't left your account.`
        : "Our team is taking a quick look before this goes through. Nothing has changed yet." })),
    h("div", { class: "card timeline" },
      step("done", money ? "Payment requested" : "Request made", when(a.createdAt)),
      step("now", "Security review", "Usually within 15 minutes. This page updates by itself."),
      step("", money ? `Sent to ${a.payee.display}` : "Done")),
    h("div", { class: "spacer" }),
    h("p", { class: "muted center", text: "Didn't start this? Cancel it, then call us on 1800 000 0000." }),
    button(money ? "Cancel payment" : "Cancel request", { kind: "danger", onClick: async () => { await cancel(a.id); toast("Cancelled. Nothing was sent."); go(`result/${a.id}`); } }),
    button("Back to home", { onClick: () => go("home") }));
}

function waiting(page, a) {
  add(page, 
    mark("warn", "lock"),
    h("div", { class: "center stack", style: { gap: "8px" } }, h("h1", { text: "Waiting for you" }),
      h("p", { class: "lede", text: "You haven't finished confirming it's you. Continue now, or cancel if you changed your mind." })),
    h("div", { class: "spacer" }),
    button("Continue", { onClick: () => go(`verify/${a.id}`) }),
    button("Cancel", { kind: "secondary", onClick: async () => { await cancel(a.id); go(`result/${a.id}`); } }));
}

const NOT_DONE = {
  DECLINED: ["We couldn't approve this", "We couldn't confirm it was you, so nothing was sent. If you didn't try this, call us on 1800 000 0000."],
  EXPIRED: ["This request timed out", "Nothing was sent. You can start again whenever you're ready."],
  CANCELLED: ["Cancelled", "Nothing was sent or changed."],
  FAILED: ["This didn't go through", "Nothing was sent."],
};

function notDone(page, a, money) {
  const [title, text] = NOT_DONE[a.status] || ["Not completed", "Nothing was sent."];
  add(page, 
    mark("bad", a.status === "CANCELLED" ? "close" : "ban"),
    h("div", { class: "center stack", style: { gap: "8px" } }, h("h1", { text: title }), h("p", { class: "lede", text: a.detail ? `${text} (${a.detail})` : text })),
    h("div", { class: "spacer" }),
    money ? button("Try again", { onClick: () => go(a.kind === "bill" ? "pay/bills" : "pay") }) : null,
    button("Back to home", { kind: money ? "secondary" : "primary", onClick: () => go("home") }));
}

async function share(a) {
  const text = `Demo Bank: ${rupees(a.amountMinor)} to ${a.payee.display} on ${when(a.createdAt)}. Reference ${a.id}.`;
  try {
    if (navigator.share) await navigator.share({ title: "Payment receipt", text });
    else { await navigator.clipboard.writeText(text); toast("Receipt copied"); }
  } catch { /* the customer closed the share sheet */ }
}

export function report(a, after = () => go(`result/${a.id}`)) {
  sheet((box, close) => {
    box.append(h("h2", { text: "Report this payment?" }),
      h("p", { class: "lede", text: `We'll tell our fraud team you didn't make the ${rupees(a.amountMinor)} payment to ${a.payee.display}. They'll call you, and may freeze your account to keep it safe.` }),
      button("Yes, I didn't make it", { kind: "danger", onClick: async () => {
        await api("POST", `/api/actions/${a.id}/report`, { reason: "" });
        close();
        toast("Reported. Our fraud team will call you shortly.");
        after();
      } }),
      button("Cancel", { kind: "secondary", onClick: close }));
  });
}
