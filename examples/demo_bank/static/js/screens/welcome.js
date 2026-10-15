import { go } from "../app.js";
import { button, h, icon } from "../ui.js";

export function render(root) {
  root.appendChild(h("div", { class: "page" },
    h("div", { class: "brandmark" }, h("span", { class: "logo" }, icon("bank", 20)), "Demo Bank"),
    h("div", { class: "hero" }, h("div", { class: "hero-art", "aria-hidden": "true" },
      h("div", { class: "disc" }),
      h("div", { class: "core" }, icon("shieldCheck", 96)),
      h("div", { class: "pill a" }, icon("face", 16), "Face"),
      h("div", { class: "pill b" }, icon("phone", 16), "SMS code"))),
    h("h1", { text: "Banking that knows it's really you" }),
    h("p", { class: "lede", text: "Everyday payments stay one tap. Anything unusual gets a quick face or code check, so nobody else can move your money." }),
    h("div", { class: "stack" },
      button("Open an account", { onClick: () => go("signup") }),
      button("I already bank here", { kind: "secondary", onClick: () => go("signin") }))));
}
