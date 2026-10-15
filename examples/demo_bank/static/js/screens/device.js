import { next, start } from "../actions.js";
import { api } from "../api.js";
import { go, signedOut } from "../app.js";
import { button, h, icon, note, topbar } from "../ui.js";

// Signed in on a device the bank hasn't seen: verify it (DEVICE_BINDING) before anything else.
export function render(root) {
  const error = h("p", { class: "form-error", role: "alert" });
  const verify = button("Verify this device", { onClick: async () => {
    verify.disabled = true;
    error.textContent = "";
    try {
      next(await start("device"), { review: false });
    } catch (e) {
      error.textContent = e.message;
      verify.disabled = false;
    }
  } });
  const out = async () => { await api("POST", "/api/logout"); signedOut(); go("welcome"); };
  root.appendChild(h("div", { class: "page" },
    topbar({ right: h("button", { type: "button", class: "link", onclick: out, text: "Sign out" }) }),
    h("div", { class: "result-mark warn" }, icon("phone", 48)),
    h("h1", { class: "center", text: "Is this your new device?" }),
    h("p", { class: "lede center", text: "This phone or computer isn't linked to your account yet. A quick check keeps your money safe if someone else has your password." }),
    note("plain", "info", "We'll ask you to confirm it's you, usually with a code and your face."),
    h("div", { class: "spacer" }),
    error,
    verify,
    h("p", { class: "muted center small", text: "Didn't sign in yourself? Sign out and change your password." })));
}
