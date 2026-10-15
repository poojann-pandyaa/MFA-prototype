// Small DOM toolkit for the bank app. Everything is built with createElement and textContent, never innerHTML.

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "style") Object.assign(el.style, v);           // CSSOM is allowed by the CSP, style="" is not
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return el;
}

// Element.append turns null into the text "null"; this skips empty slots like h() does.
export function add(parent, ...kids) {
  parent.append(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
  return parent;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

const SVG = "http://www.w3.org/2000/svg";
const ICONS = {
  back: "M15 18l-6-6 6-6",
  close: "M6 6l12 12M18 6L6 18",
  chevron: "M9 6l6 6-6 6",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z",
  bill: "M6 2h12v20l-3-2-3 2-3-2-3 2V2zM9 7h6M9 11h6M9 15h3",
  payee: "M9 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM2 21c1-4 4-6 7-6s6 2 7 6M19 8v6M16 11h6",
  shield: "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3z",
  shieldCheck: "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3zM8.5 12.2l2.4 2.4 4.6-4.8",
  shieldClock: "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3zM12 8v4l2.5 1.5",
  shieldAlert: "M12 2.5l7.5 3v6c0 4.6-3.2 8.4-7.5 10-4.3-1.6-7.5-5.4-7.5-10v-6l7.5-3zM12 8v4M12 15.5v.5",
  home: "M3 11l9-8 9 8v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V11z",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  profile: "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 21c1.5-4 4.5-6 8-6s6.5 2 8 6",
  check: "M5 12.5l4.5 4.5L19 7.5",
  lock: "M5 11h14v10H5zM8 11V8a4 4 0 0 1 8 0v3",
  warn: "M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17v.5",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8v4M12 16v.5",
  plus: "M12 5v14M5 12h14",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  eyeOff: "M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7c1.6 0 3-.4 4.3-1",
  mic: "M9 2.5h6v12H9zM5 11a7 7 0 0 0 14 0M12 18v3.5",
  keyboard: "M2.5 6h19v12h-19zM6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10",
  phone: "M5 2.5h14v19H5zM10 18.5h4",
  face: "M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M12 8a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM8.5 16.5c.9-1.4 2-2 3.5-2s2.6.6 3.5 2",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7l1-8z",
  drop: "M12 22c4 0 7-3 7-7 0-5-7-13-7-13S5 10 5 15c0 4 3 7 7 7z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5",
  bell: "M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 20a2 2 0 0 0 4 0",
  bank: "M3 10h18M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18M12 3l9 5H3l9-5z",
  ban: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM5.6 5.6l12.8 12.8",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z",
  del: "M21 5H9l-7 7 7 7h12a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1zM17 9l-6 6M11 9l6 6",
  key: "M15 7a4 4 0 1 1-3.5 6L4 20.5V17h3v-3h3l1.5-1.5A4 4 0 0 1 15 7z",
  laptop: "M4 5h16v11H4zM2 19h20",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
  arrowLeft: "M19 12H5M11 6l-6 6 6 6",
  arrowRight: "M5 12h14M13 6l6 6-6 6",
};

export function icon(name, size = 22, label) {
  const svg = document.createElementNS(SVG, "svg");
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" })) svg.setAttribute(k, v);
  if (label) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label); } else svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("d", ICONS[name] || ICONS.info);
  svg.appendChild(p);
  return svg;
}

function tone(text) {
  let n = 0;
  for (const ch of text) n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  return n % 4;
}

export function avatar(initials, size = "") {
  return h("span", { class: `avatar ${size} tone-${tone(initials || "?")}`, "aria-hidden": "true", text: initials || "?" });
}

export function tile(name, variant = "") {
  return h("span", { class: `tile ${variant}` }, icon(name, 22));
}

export function button(text, { kind = "primary", onClick, type = "button", disabled, iconName, cls = "" } = {}) {
  return h("button", { type, class: `btn ${kind} ${cls}`, onclick: onClick, disabled }, iconName ? icon(iconName, 20) : null, text);
}

export function iconButton(name, label, onClick) {
  return h("button", { type: "button", class: "icon-btn", "aria-label": label, onclick: onClick }, icon(name, 20));
}

export function topbar({ title, back, close, label, right }) {
  const lead = back ? iconButton("back", "Back", back) : close ? iconButton("close", "Close", close) : null;
  const pad = () => h("span", { style: { width: "44px", flexShrink: "0" } });   // keeps a centred label centred
  return h("div", { class: "topbar" },
    lead || (label ? pad() : null),
    title ? h("h2", { text: title }) : label ? h("span", { class: "label", text: label }) : h("span", { class: "spacer" }),
    right || (label ? pad() : null));
}

export function stepsBar(done, total) {
  return h("div", { class: "steps-bar", "aria-hidden": "true" },
    Array.from({ length: Math.max(total, 1) }, (_, i) => h("span", { class: i < done ? "on" : "" })));
}

export function item({ lead, title, sub, trail, trailClass = "", onClick, chevron, subClass = "" }) {
  const body = h("span", { class: "body" }, h("span", { class: "title", text: title }),
    sub ? (typeof sub === "string" ? h("span", { class: `sub ${subClass}`, text: sub }) : sub) : null);
  const kids = [lead, body, trail !== undefined && trail !== null ? (typeof trail === "string" ? h("span", { class: `trail ${trailClass}`, text: trail }) : trail) : null,
    chevron ? icon("chevron", 18) : null];
  return onClick ? h("button", { type: "button", class: "item", onclick: onClick }, kids) : h("div", { class: "item" }, kids);
}

export function list(items) {
  return h("div", { class: "list" }, items);
}

export function chip(text, kind = "") {
  return h("span", { class: `chip ${kind}`, text });
}

export function note(kind, iconName, ...content) {
  return h("div", { class: `note ${kind}`, role: kind === "bad" ? "alert" : null }, icon(iconName, 20), h("span", {}, content));
}

// `control` is the element the label points at when `input` is a wrapper (prefix, show-password button)
export function field({ label, id, input, control, err }) {
  (control || input).id = id;
  return h("div", { class: "field" }, h("label", { for: id, text: label }), input, err || null);
}

export function sheet(build) {
  const scrim = h("div", { class: "scrim", role: "dialog", "aria-modal": "true" });
  const box = h("div", { class: "sheet" }, h("span", { class: "grip", "aria-hidden": "true" }));
  scrim.appendChild(box);
  const close = () => { scrim.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  scrim.addEventListener("click", (e) => { if (e.target === scrim) close(); });
  document.addEventListener("keydown", onKey);
  build(box, close);
  document.body.appendChild(scrim);
  const first = box.querySelector("button, input, select, a");
  if (first) first.focus();
  return { close };
}

let toastTimer = null;
export function toast(text) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = h("div", { class: "toast", role: "status", text });
  document.body.appendChild(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 3200);
}

export function keypad(onKey) {
  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "del"];
  return h("div", { class: "keypad" }, keys.map((k) => k === "del"
    ? h("button", { type: "button", class: "flat", "aria-label": "Delete", onclick: () => onKey(k) }, icon("del", 26))
    : h("button", { type: "button", class: k === "." ? "flat" : "", onclick: () => onKey(k), text: k })));
}

// "V. Mehta ••4411" -> "Mehta", "City Electricity" -> "City": a short name for an avatar caption.
export function shortName(display) {
  const words = (display || "").replace(/[•\d]/g, " ").split(/\s+/).filter(Boolean);
  const first = words.find((w) => w.replace(/\./g, "").length > 1) || words[0] || "";
  return first.replace(/\.$/, "");
}

// Amount keypad typing: digits, one dot, two decimals, eight whole digits, no leading zeros.
export function press(text, key) {
  if (key === "del") return text.slice(0, -1);
  if (key === ".") return text.includes(".") ? text : (text || "0") + ".";
  const [whole, dec] = text.split(".");
  if (dec !== undefined) return dec.length >= 2 ? text : text + key;
  if (whole === "0") return key;
  return whole.length >= 8 ? text : text + key;
}

export function rupees(minor, { decimals = true } = {}) {
  const v = (minor || 0) / 100;
  return "₹" + v.toLocaleString("en-IN", { minimumFractionDigits: decimals && v % 1 ? 2 : 0, maximumFractionDigits: 2 });
}

export function rupeesParts(minor) {
  const v = (minor || 0) / 100;
  const whole = Math.floor(v).toLocaleString("en-IN");
  return [whole, "." + String(Math.round((v % 1) * 100)).padStart(2, "0")];
}

const DAY = 86400000;
export function dayLabel(ts) {
  const d = new Date(ts * 1000), today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (d.getTime() >= start) return "Today";
  if (d.getTime() >= start - DAY) return "Yesterday";
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function timeOf(ts) {
  return new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

export function when(ts) {
  return `${dayLabel(ts)}, ${timeOf(ts)}`;
}

// how each status reads to a customer
export const STATUS = {
  AWAITING_VERIFICATION: ["Waiting for you", "warn"],
  UNDER_REVIEW: ["Being reviewed", "warn"],
  DONE: ["Done", "ok"],
  DECLINED: ["Not sent, check failed", "bad"],
  EXPIRED: ["Expired", "bad"],
  CANCELLED: ["Cancelled", ""],
  FAILED: ["Didn't go through", "bad"],
};

export const MONEY = new Set(["transfer", "bill"]);
