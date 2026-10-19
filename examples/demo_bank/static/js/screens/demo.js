// "Behind the scenes": for a presenter only (BANK_DEMO_TOOLS). Shows what AMFA decided for the last action and
// stages the signals a real bank gets from its own systems (SIM swap reports, mule lists, a new phone).
import { api } from "../api.js";
import { go, randomId, refresh, render as rerender, state } from "../app.js";
import { add, button, chip, h, icon, sheet, toast } from "../ui.js";

const LEVELS = ["T0", "T1", "T2", "T3", "T4"];

export async function open() {
  const { decision: d } = await api("GET", "/api/demo/last-decision");
  sheet((box, close) => {
    add(box,
      h("div", { class: "row-between" },
        h("div", { class: "row-between", style: { gap: "8px" } }, h("h2", { text: "Behind the scenes" }), chip("DEMO", "demo")),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Close", onclick: close }, icon("close", 18))),
      d ? decision(d) : h("div", { class: "card empty", style: { background: "var(--bg)" } },
        h("strong", { text: "No decision yet" }), h("span", { text: "Make a payment or change something, then open this again." })),
      stage(close));
  });
}

function decision(d) {
  const max = Math.max(...d.reasons.map((r) => r.weight), 0.0001);
  return h("div", { class: "card stack", style: { background: "var(--bg)" } },
    h("div", { class: "row-between" }, h("span", { class: "muted", text: "Last decision" }), h("strong", { class: "small", text: d.title })),
    h("div", {}, h("div", { class: "muted small", style: { marginBottom: "6px" }, text: "Threat level" }),
      h("div", { class: "levels" }, LEVELS.map((l) => h("span", { class: l === d.threatLevel ? "on" : "", text: l }))),
      h("div", { class: "muted small", style: { marginTop: "6px" },
        text: `Fraud probability ${d.score === null || d.score === undefined ? "n/a" : d.score.toFixed(2)} · model ${d.modelVersion || "none"}` }),
      d.modelLevel && d.modelLevel !== d.threatLevel ? h("div", { class: "muted small", text: `Model said ${d.modelLevel}; ${d.capReason || "adjusted"}` }) : null),
    d.reasons.length ? h("div", {}, h("div", { class: "muted small", style: { marginBottom: "8px" }, text: "Why" }),
      h("div", { class: "why" }, d.reasons.flatMap((r) => [h("span", { text: r.text.charAt(0).toUpperCase() + r.text.slice(1) }),
        h("span", { class: "bar" }, h("span", { style: { width: Math.round(100 * r.weight / max) + "%" } })),
        h("b", { text: "+" + r.weight.toFixed(2) })]))) : null,
    h("div", { class: "row-between small" }, h("span", { class: "muted", text: "Checks asked" }), h("span", { class: "chips" }, d.checks.map((c) => chip(c)))),
    h("div", { class: "row-between small" }, h("span", { class: "muted", text: "Old rule table would ask" }),
      h("strong", { text: d.rulesBaseline.length ? d.rulesBaseline.join(" + ") : "nothing extra" })),
    d.escalate ? h("div", { class: "row-between small" }, h("span", { class: "muted", text: "Sent to" }), h("strong", { text: "Manual review" })) : null);
}

function stage(close) {
  const me = state.me;
  const payee = h("select", { class: "input", "aria-label": "Payee" }, me.payees.map((p) => h("option", { value: p.ref, text: p.display })));
  let newPhone = false;
  try { newPhone = !!sessionStorage.getItem("bank.demoDevice"); } catch { /* private mode */ }
  const phone = h("input", { type: "checkbox", checked: newPhone });
  phone.addEventListener("change", async () => {
    try {
      if (phone.checked) sessionStorage.setItem("bank.demoDevice", randomId("demo-phone-"));
      else sessionStorage.removeItem("bank.demoDevice");
    } catch { /* private mode */ }
    close();
    await refresh();
    toast(phone.checked ? "This browser now looks like a new phone." : "Back to your usual device.");
    go("home");
    rerender();
  });
  const act = (label, path, body, done) => button(label, { kind: "secondary", cls: "small", onClick: async () => {
    await api("POST", path, body);
    await refresh();
    toast(done);
    rerender();
  } });
  return h("div", { class: "stack" },
    h("h3", { text: "Stage an attack" }),
    h("p", { class: "muted small", text: "Then make a payment and watch the decision change." }),
    act("SIM swapped just now", "/api/demo/sim-swap", {}, "SIM swap recorded. Try a payment now."),
    h("div", { class: "row-between" }, payee,
      button("Mule list", { kind: "secondary", cls: "small", onClick: async () => {
        await api("POST", "/api/demo/payee-risk", { payeeRef: payee.value, flagged: true });
        toast("That payee is now on a mule-account list.");
      } })),
    h("label", { class: "toggle-row" }, "Pretend this is a new phone", phone),
    me.frozen ? act("Unfreeze account (support desk)", "/api/demo/unfreeze", {}, "Account unfrozen.") : null,
    button("Reset demo signals", { kind: "secondary", cls: "small", onClick: async () => {
      await api("POST", "/api/demo/reset");
      try { sessionStorage.removeItem("bank.demoDevice"); } catch { /* private mode */ }
      await refresh();
      close();
      toast("Demo signals cleared.");
      rerender();
    } }));
}
