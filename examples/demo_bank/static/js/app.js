// Demo Bank app shell: boot, device identity, hash router with the account guards, bottom tabs, demo button.
import { api } from "./api.js";
import { button, clear, h, icon, note } from "./ui.js";

export const state = { me: null, signedIn: false, amfaOrigin: null, config: null, deviceId: null, draft: {} };

const screen = document.getElementById("screen");
const PUBLIC = new Set(["welcome", "signup", "signin"]);
const TAB_OF = { home: "home", pay: "pay", activity: "activity", security: "profile" };
const DEVICE_OK = new Set(["device", "verify", "result"]);   // what an unverified device may open

// ---------- device identity: AMFA's device page gives a stable id; demo mode can pretend to be a new phone ----------
let resolveDevice;
const deviceReady = new Promise((r) => { resolveDevice = r; });

function remembered(key, make) {
  try {
    const v = localStorage.getItem(key) || make();
    localStorage.setItem(key, v);
    return v;
  } catch { return make(); }
}

export function randomId(prefix) {
  const a = crypto.getRandomValues(new Uint8Array(12));
  return prefix + Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function currentDevice() {
  try { return sessionStorage.getItem("bank.demoDevice") || state.deviceId; } catch { return state.deviceId; }
}

function startDevice(cfg) {
  window.addEventListener("message", (e) => {
    if (e.origin === state.amfaOrigin && e.data && e.data.type === "amfa:device" && !state.deviceId) {
      state.deviceId = e.data.deviceId;
      resolveDevice();
    }
  });
  const f = h("iframe", { src: `${cfg.amfaBase}/hosted/device?t=${encodeURIComponent(cfg.tenantSlug || "")}`, class: "hidden",
    title: "device", "aria-hidden": "true", sandbox: "allow-scripts allow-same-origin" });
  document.body.appendChild(f);
  setTimeout(() => {   // AMFA unreachable: an id kept by this browser
    if (!state.deviceId) { state.deviceId = remembered("bank.device", () => randomId("web-")); resolveDevice(); }
  }, 3000);
}

// ---------- data ----------
export async function refresh() {
  await deviceReady;
  state.me = await api("GET", "/api/me?deviceId=" + encodeURIComponent(currentDevice()));
  state.signedIn = true;
  return state.me;
}

export function signedOut() {
  state.me = null;
  state.signedIn = false;
}

// ---------- routing ----------
export function go(path) {
  if (location.hash === "#/" + path) render(); else location.hash = "#/" + path;
}

function pick(name) {
  if (!state.signedIn) return PUBLIC.has(name) ? name : "welcome";
  const me = state.me;
  if (me.frozen) return "frozen";
  if (me.protection.have.length < 2) return "setup";
  if (!me.deviceTrusted && !DEVICE_OK.has(name)) return "device";
  if (!name || PUBLIC.has(name) || name === "setup" || name === "frozen" || (name === "device" && me.deviceTrusted)) return "home";
  return name;
}

let rendering = 0;
export async function render() {
  const ticket = ++rendering;
  const [name, ...params] = location.hash.replace(/^#\/?/, "").split("/");
  const route = pick(name);
  try {
    const mod = await import(`./screens/${route}.js`);
    if (ticket !== rendering) return;
    clear(screen);
    window.scrollTo(0, 0);
    await mod.render(screen, params.map(decodeURIComponent));
  } catch (e) {
    if (e.status === 401) { signedOut(); return go("signin"); }
    clear(screen).appendChild(h("div", { class: "page" }, h("div", { class: "spacer" }),
      note("bad", "warn", e.message || "Something went wrong."),
      button("Try again", { onClick: () => render() }), h("div", { class: "spacer" })));
  }
  tabs(TAB_OF[route]);
  demoButton(TAB_OF[route] ? "" : TOP_DEMO.has(route) ? "top" : null);
}
const TOP_DEMO = new Set(["review", "result", "frozen"]);   // screens with their own bottom buttons: the demo button sits at the top

// ---------- bottom tabs and the demo button live outside the screen ----------
const TABS = [["home", "Home", "home", "home"], ["pay", "Pay", "send", "pay"], ["activity", "Activity", "activity", "activity"],
  ["profile", "Profile", "profile", "security"]];
let tabBar = null;
function tabs(on) {
  if (tabBar) tabBar.remove();
  tabBar = null;
  if (!on) return;
  tabBar = h("div", { class: "tabs" }, h("nav", { "aria-label": "Main" }, TABS.map(([key, label, ic, route]) =>
    h("a", { href: "#/" + route, class: key === on ? "on" : "", "aria-current": key === on ? "page" : null }, icon(ic, 22), label))));
  document.body.appendChild(tabBar);
}

let fab = null;
function demoButton(place) {   // place: "" above the tabs, "top", or null for none
  if (fab) fab.remove();
  fab = null;
  if (place === null || !state.signedIn || !state.me.demoTools) return;
  fab = h("button", { type: "button", class: `demo-fab ${place}`, "aria-label": "Behind the scenes",
    onclick: () => import("./screens/demo.js").then((m) => m.open()) },
    icon("spark", 16), place === "top" ? null : "Behind the scenes");
  document.body.appendChild(fab);
}

// ---------- boot ----------
async function boot() {
  state.config = await fetch("/assets/config.json").then((r) => r.json());
  state.amfaOrigin = new URL(state.config.amfaBase).origin;
  startDevice(state.config);
  try {
    await refresh();
  } catch (e) {
    if (e.status !== 401) throw e;
    signedOut();
  }
  window.addEventListener("hashchange", render);
  render();
}

boot().catch((e) => {
  clear(screen).appendChild(h("div", { class: "page" }, note("bad", "warn", e.message || "The app couldn't start.")));
});
