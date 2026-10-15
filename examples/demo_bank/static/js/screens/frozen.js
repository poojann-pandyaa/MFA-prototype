import { api } from "../api.js";
import { go, signedOut } from "../app.js";
import { button, h, icon, note } from "../ui.js";

export function render(root) {
  root.appendChild(h("div", { class: "page" },
    h("div", { class: "spacer" }),
    h("div", { class: "result-mark bad" }, h("div", { class: "dot" }, icon("lock", 40))),
    h("h1", { class: "center", text: "Your account is frozen" }),
    h("p", { class: "lede center", text: "Nothing can be paid or changed until we unfreeze it. Your money is safe." }),
    note("plain", "info", "Call us on 1800 000 0000 from your registered mobile. We'll check it's you and unfreeze it."),
    h("div", { class: "spacer" }),
    h("a", { class: "btn primary", href: "tel:18000000000" }, icon("phone", 20), "Call 1800 000 0000"),
    button("Sign out", { kind: "secondary", onClick: async () => {
      await api("POST", "/api/logout");
      signedOut();
      go("welcome");
    } })));
}
