// Full-screen host for AMFA's own setup and verification pages. AMFA captures the face, voice and codes on
// its origin; this layer gives it the bank's header and step progress so it feels like one app.
import { state } from "./app.js";
import { h, icon, iconButton, stepsBar } from "./ui.js";

const STEP_NAMES = { OTP: "code", FACE: "face", VOICE: "voice", UEBA: "typing", CONSENT: "consent", DEVICE: "this device" };

export function openAmfa(url, { flow = "session", title = "Confirm it's you" } = {}) {
  return new Promise((resolve) => {
    const label = h("span", { class: "label", text: title });
    // setting up and verifying look alike inside the frame; the header says which one this is
    const tag = h("div", { class: `flow-tag ${flow === "enroll" ? "enroll" : "verify"}` },
      h("strong", { text: flow === "enroll" ? "One-time setup" : "Verification" }),
      h("span", { text: flow === "enroll" ? "Registering how we'll recognise you. No payment is made." : "Approving this request. Nothing is sent until you pass." }));
    const bars = h("div", {}, stepsBar(0, 1));
    const frame = h("iframe", { src: url, title, allow: "camera; microphone", referrerpolicy: "no-referrer",
      sandbox: "allow-scripts allow-same-origin allow-forms" });
    const layer = h("div", { class: "amfa-layer", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "head" },
        h("div", { class: "topbar" }, iconButton("close", "Close", () => finish({ closed: true })), label, h("span", { style: { width: "44px" } })),
        tag,
        bars),
      frame,
      h("div", { class: "foot" }, icon("lock", 14), "Secured by Demo Bank · nothing is recorded or kept"));

    function finish(result) {
      window.removeEventListener("message", onMessage);
      layer.remove();
      resolve(result);
    }

    function onMessage(e) {
      if (e.origin !== state.amfaOrigin || e.source !== frame.contentWindow || !e.data || typeof e.data !== "object") return;
      const d = e.data;
      if (d.type === "amfa:step" && d.flow === flow && d.total > 0) {
        const n = Math.min(d.index + 1, d.total);
        label.textContent = `${title} · ${n} of ${d.total}${STEP_NAMES[d.factor] ? " · " + STEP_NAMES[d.factor] : ""}`;
        bars.replaceChildren(stepsBar(n, d.total));
      }
      // long enough to read the outcome on AMFA's page before the bank shows its own result screen
      if (d.type === "amfa:status" && d.status !== "PENDING") setTimeout(() => finish({ status: d.status }), 1800);
      if (d.type === "amfa:enrolled") finish({ enrolled: true, factors: d.factors || [] });
    }

    window.addEventListener("message", onMessage);
    document.body.appendChild(layer);
  });
}
