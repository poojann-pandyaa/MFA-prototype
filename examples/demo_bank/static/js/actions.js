// Starting protected actions and reading them back. Every action goes: start (AMFA decides) -> review ->
// verify (AMFA's checks, full screen) -> result.
import { api } from "./api.js";
import { currentDevice, go, refresh } from "./app.js";

function capabilities() {
  const media = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  return { camera: media, microphone: media, pointer: matchMedia("(pointer: fine)").matches };
}

export async function start(kind, body = {}) {
  return api("POST", `/api/actions/${kind}`, { ...body, deviceId: currentDevice(), ...capabilities() });
}

// After starting: held for review goes straight to the result; otherwise review first (or verify directly).
export function next(started, { review = true } = {}) {
  const a = started.action;
  if (a.status === "UNDER_REVIEW" || !started.challengeUrl) return go(`result/${a.id}`);
  return go(review ? `review/${a.id}` : `verify/${a.id}`);
}

export async function find(id) {
  const { activity, balance } = await api("GET", "/api/activity");
  const a = activity.find((x) => x.id === id);
  if (!a) {
    const e = new Error("We couldn't find that request.");
    e.status = 404;
    throw e;
  }
  return { action: a, balance };
}

export async function cancel(id) {
  const { action } = await api("POST", `/api/actions/${id}/cancel`);
  await refresh();
  return action;
}
