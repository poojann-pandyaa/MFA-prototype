import { api } from "../api.js";
import { go, refresh, state } from "../app.js";
import { openAmfa } from "../amfa-frame.js";
import { button, chip, h, item, list, note, stepsBar, tile, toast, topbar } from "../ui.js";

// Progressive setup: one-time codes go to the email given at sign-up, so the face is the only thing to set up now.
export function render(root) {
  const me = state.me;
  const byEmail = (me.codeTo || "").includes("@");
  const hasCode = me.protection.have.some((w) => w.endsWith("code"));
  const hasFace = me.protection.have.includes("Face");
  const error = h("p", { class: "form-error", role: "alert" });
  const go_ = button(hasFace ? "Continue" : "Set up my face", { onClick: begin });

  async function begin() {
    go_.disabled = true;
    error.textContent = "";
    try {
      const { url } = await api("POST", "/api/amfa/enrol", { factors: ["FACE"] });
      const res = await openAmfa(url, { flow: "enroll", title: "Set up" });
      await refresh();
      if (state.me.protection.have.length >= 2) {
        toast("You're all set. Welcome to Demo Bank.");
        return go("home");
      }
      if (!res.closed) error.textContent = "Your face isn't set up yet. Please try again.";
    } catch (e) {
      error.textContent = e.message;
    }
    go_.disabled = false;
  }

  root.appendChild(h("div", { class: "page" },
    topbar({ label: "Step 1 of 2 · about 1 minute" }),
    stepsBar(1, 2),
    h("h1", { text: "Let's protect your account" }),
    h("p", { class: "lede", text: "Two things, then you can start banking. We'll only ask for a check when a payment looks unusual." }),
    list([
      item({ lead: tile(byEmail ? "mail" : "phone"), title: byEmail ? "Codes by email" : "SMS codes",
        sub: `Sent to ${me.codeTo}`, trail: chip(hasCode ? "Ready" : "Setting up", hasCode ? "ok" : "") }),
      item({ lead: tile("face"), title: "Your face", sub: "Five quick pictures, about 30 seconds",
        trail: chip(hasFace ? "Ready" : "Next", hasFace ? "ok" : "") }),
    ]),
    note("brand", "lock", "Your photos never reach the bank. Only an encrypted template is kept, and Demo Bank will delete it if you ask."),
    h("div", { class: "spacer" }),
    error,
    go_,
    h("p", { class: "muted center small", text: "You'll confirm your consent on the next screen. Voice and typing can be added later for extra protection." })));
}
