// Calls to the bank's own server. Errors carry .status and a message a customer can read.

const FRIENDLY = {
  401: "Please sign in again.",
  502: "We can't reach the security service right now. Nothing was sent. Please try again in a moment.",
};

export async function api(method, path, body) {
  let r;
  try {
    r = await fetch(path, {
      method, credentials: "same-origin",
      headers: body !== undefined ? { "content-type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    const e = new Error("You seem to be offline. Check your connection and try again.");
    e.status = 0;
    throw e;
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = typeof data.detail === "string" ? data.detail : null;
    const e = new Error(FRIENDLY[r.status] || (detail ? detail.charAt(0).toUpperCase() + detail.slice(1) + "." : "Something went wrong. Please try again."));
    e.status = r.status;
    throw e;
  }
  return data;
}
