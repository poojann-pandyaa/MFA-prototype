import { api } from "../api.js";
import { go, refresh, signedOut, state } from "../app.js";
import { openAmfa } from "../amfa-frame.js";
import { add, button, chip, h, icon, item, list, rupees, sheet, tile, toast, topbar } from "../ui.js";

const WAYS = [
  ["SMS code", "phone", "Codes sent to your mobile", null],
  ["Face", "face", "A quick head turn", "FACE"],
  ["Voice", "mic", "Read three short sentences", "VOICE"],
  ["Typing", "keyboard", "Type a phrase a few times", "UEBA"],
  ["Authenticator app", "key", "Optional, works without signal", "OTP"],
];
const LEVEL_TONE = { Strong: "ok", Good: "warn", Basic: "bad" };

export function render(root) {
  const me = state.me;
  const p = me.protection;
  const strength = p.have.length;

  async function addWay(factor, name) {
    try {
      const { url } = await api("POST", "/api/amfa/enrol", { factors: [factor] });
      const res = await openAmfa(url, { flow: "enroll", title: `Add ${name.toLowerCase()}` });
      await refresh();
      if (res.enrolled) toast(`${name} added. Your protection is now ${state.me.protection.level}.`);
      go("security");
    } catch (e) {
      toast(e.message);
    }
  }

  const ways = WAYS.map(([name, ic, sub, factor]) => {
    const on = p.have.includes(name);
    const can = p.canAdd.includes(name);
    if (!on && !can) return null;
    return item({ lead: tile(ic, on ? "" : "grey"), title: name, sub: on ? null : sub,
      trail: on ? chip("On", "ok") : button("Add", { kind: "soft", cls: name === p.canAdd[0] ? "solid" : "",
        onClick: () => addWay(factor, name) }) });
  }).filter(Boolean);

  root.appendChild(h("div", { class: "page with-tabs" },
    topbar({ title: "Security" }),
    h("div", { class: "card stack" },
      h("div", { class: "row-between" }, h("strong", { text: "Protection level" }), chip(p.level, LEVEL_TONE[p.level])),
      h("div", { class: "meter-4", "aria-hidden": "true" }, [0, 1, 2, 3].map((i) => h("span", { class: i < strength ? "on" : "" }))),
      h("p", { class: "muted small", text: `${strength} of 4 ways to confirm it's you. More ways means fewer stops when you're travelling or on a new phone.` })),
    h("div", { class: "section-label", text: "Ways to confirm it's you" }),
    list(ways),
    h("div", { class: "section-label", text: "Account" }),
    list([
      item({ lead: tile("laptop", "grey"), title: "Trusted devices", sub: `This device${me.devices > 1 ? ` and ${me.devices - 1} more` : ""}` }),
      item({ lead: tile("send", "grey"), title: "Daily limit", sub: `${rupees(me.dailyLimit)} a day`, chevron: true, onClick: () => go("limit") }),
      item({ lead: tile("phone", "grey"), title: "Mobile and email", sub: [me.phone ? `+91 ${me.phone}` : null, me.email].filter(Boolean).join(" · ") || "Not set",
        chevron: true, onClick: () => go("contact") }),
      item({ lead: tile("key", "grey"), title: "Password", sub: "Change your password", chevron: true, onClick: () => go("password") }),
    ]),
    button("Freeze my account", { kind: "danger", iconName: "lock", onClick: freeze }),
    h("button", { type: "button", class: "link center", text: "Sign out", onclick: async () => {
      await api("POST", "/api/logout");
      signedOut();
      go("welcome");
    } })));
}

function freeze() {
  sheet((box, close) => add(box,
    h("h2", { text: "Freeze your account?" }),
    h("p", { class: "lede", text: "Nothing can be paid or changed until you call us to unfreeze it. Do this if your phone is lost or someone else may have your details." }),
    button("Freeze now", { kind: "danger", onClick: async () => {
      await api("POST", "/api/account/freeze");
      close();
      await refresh();
      go("frozen");
    } }),
    button("Keep it open", { kind: "secondary", onClick: close })));
}
