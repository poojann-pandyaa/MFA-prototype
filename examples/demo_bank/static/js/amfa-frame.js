// Full-screen host for AMFA's own setup and verification pages. AMFA captures the face, voice and codes on
// its origin; this layer gives it the bank's header and step progress so it feels like one app.
import { api } from "./api.js";
import { state } from "./app.js";
import { h, icon, iconButton, stepsBar } from "./ui.js";

const STEP_NAMES = { OTP: "code", FACE: "face", VOICE: "voice", UEBA: "typing", CONSENT: "consent", DEVICE: "this device" };

// On a demo server no email or text is sent: the bank keeps the latest code for the signed-in customer and shows it on request.
function demoCode() {
  if (!state.me || !state.me.demoTools) return null;
  const out = h("strong", { style: { marginLeft: "6px" } });
  const btn = h("button", { type: "button", class: "link small", text: "Show demo code", style: { marginLeft: "10px" } });
  btn.addEventListener("click", async () => {
    try {
      const r = await api("GET", "/api/demo/code");
      out.textContent = r.code ? `Your code: ${r.code}` : "No code yet: ask for one first.";
    } catch (e) { out.textContent = e.message; }
  });
  return h("span", {}, btn, out);
}

export function openAmfa(url, { flow = "session", title = "Confirm it's you" } = {}) {
  return new Promise((resolve) => {
    const label = h("span", { class: "label", text: title });
    const bars = h("div", {}, stepsBar(0, 1));
    const frame = h("iframe", { src: url, title, allow: "camera; microphone", referrerpolicy: "no-referrer",
      sandbox: "allow-scripts allow-same-origin allow-forms" });
    const layer = h("div", { class: "amfa-layer", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "head" },
        h("div", { class: "topbar" }, iconButton("close", "Close", () => finish({ closed: true })), label, h("span", { style: { width: "44px" } })),
        bars),
      frame,
      h("div", { class: "foot", style: { flexWrap: "wrap" } }, icon("lock", 14), "Secured by Demo Bank · nothing is recorded or kept", demoCode()));

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
      if (d.type === "amfa:status" && d.status !== "PENDING") setTimeout(() => finish({ status: d.status }), 400);
      if (d.type === "amfa:enrolled") finish({ enrolled: true, factors: d.factors || [] });
    }

    window.addEventListener("message", onMessage);
    document.body.appendChild(layer);
  });
}
