import { api } from "../api.js";
import { go, refresh } from "../app.js";
import { openAmfa } from "../amfa-frame.js";
import { h, icon, toast } from "../ui.js";

// Opens AMFA's checks full screen for this action (first time, or continuing after the customer closed them),
// then asks the bank to settle it and shows the result.
export async function render(root, [id]) {
  root.appendChild(h("div", { class: "page" }, h("div", { class: "spacer" }),
    h("div", { class: "center stack" }, h("div", { class: "result-mark warn" }, icon("lock", 44)), h("h2", { text: "Opening a secure check…" })),
    h("div", { class: "spacer" })));
  let url;
  try {
    ({ challengeUrl: url } = await api("POST", `/api/actions/${id}/continue`));
  } catch (e) {
    if (e.status === 409) return go(`result/${id}`);
    throw e;
  }
  const res = await openAmfa(url, { flow: "session", title: "Confirm it's you" });
  if (res.closed) {
    toast("Saved. You can finish it from Activity.");
    return go("activity");
  }
  await api("POST", `/api/actions/${id}/complete`);
  await refresh();
  go(`result/${id}`);
}
