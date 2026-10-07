(() => {
  "use strict";
  const API = "/admin/api";
  const CSRF_KEY = "amfa.csrf";
  const TENANT_KEY = "amfa.tenant";
  const REFRESH_MS = 1e4;
  const WEBHOOK_EVENTS = [ "transaction.authorized", "transaction.denied", "transaction.review_required", "transaction.expired", "review.resolved", "security.event", "customer.enrolled" ];
  const DEFAULT_SCOPES = [ "users:write", "users:read", "transactions:write", "transactions:read", "proofs:verify", "events:read" ];
  const FACTORS = [ "OTP", "UEBA", "FACE", "VOICE" ];
  const SEGMENTS = [ "STANDARD", "PREMIUM", "SENIOR", "CORPORATE", "NEW_TO_BANK", "WATCHLIST" ];
  const state = {
    me: null,
    tenant: null,
    view: null,
    txnTimer: null,
    decisionFilter: "",
    policy: null,
    draft: null,
    dirty: false,
    keysScopes: null
  };
  const $ = id => document.getElementById(id);
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "class") node.className = v; else if (k === "text") node.textContent = String(v); else if (k === "for") node.htmlFor = v; else if (k === "dataset") Object.assign(node.dataset, v); else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v); else if (k === "value") node.value = v; else if (k === "checked" || k === "disabled" || k === "hidden" || k === "required") node[k] = Boolean(v); else node.setAttribute(k, v === true ? "" : String(v));
      }
    }
    append(node, children);
    return node;
  }
  function append(node, children) {
    for (const c of children) {
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) append(node, c); else if (c instanceof Node) node.appendChild(c); else node.appendChild(document.createTextNode(String(c)));
    }
    return node;
  }
  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }
  function replace(node, ...children) {
    clear(node);
    return append(node, children);
  }
  function showError(node, err) {
    if (!node) return;
    if (!err) {
      node.hidden = true;
      clear(node);
      return;
    }
    replace(node, ...errorNodes(err));
    node.hidden = false;
  }
  function errorNodes(err) {
    const out = [];
    const code = err && err.code ? err.code : "ERROR";
    const msg = err && err.message ? err.message : String(err);
    out.push(el("strong", {
      text: code
    }), ": ", msg);
    if (err && Array.isArray(err.fields) && err.fields.length) {
      out.push(el("ul", null, err.fields.map(f => el("li", {
        text: `${(f.loc || []).filter(x => x !== "body").join(".") || "field"}: ${f.msg || ""}`
      }))));
    }
    return out;
  }
  function errorBox(err) {
    return el("div", {
      class: "error",
      role: "alert"
    }, errorNodes(err));
  }
  function loading() {
    return el("p", {
      class: "muted",
      role: "status",
      text: "Loading…"
    });
  }
  function fmtTime(unix) {
    if (unix === null || unix === undefined || unix === "") return "—";
    const n = typeof unix === "number" ? unix : Number(unix);
    const d = Number.isFinite(n) ? new Date(n * 1e3) : new Date(String(unix));
    if (Number.isNaN(d.getTime())) return String(unix);
    const opts = {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    };
    if (d.getFullYear() !== (new Date).getFullYear()) opts.year = "numeric";
    return d.toLocaleString(undefined, opts);
  }
  function timeCell(unix) {
    const n = Number(unix);
    const attrs = {
      class: "nowrap"
    };
    if (Number.isFinite(n) && n > 0) attrs.title = new Date(n * 1e3).toISOString();
    return el("span", attrs, fmtTime(unix));
  }
  const STATUS_CLASS = {
    PASSED: "s-ok",
    VERIFIED: "s-ok",
    AUTHORIZED: "s-ok",
    APPROVED: "s-ok",
    ACTIVE: "s-ok",
    DELIVERED: "s-ok",
    SUCCEEDED: "s-ok",
    OK: "s-ok",
    UP: "s-ok",
    LOW: "s-ok",
    INTACT: "s-ok",
    FAILED: "s-bad",
    DENIED: "s-bad",
    BLOCKED: "s-bad",
    REJECTED: "s-bad",
    LOCKED: "s-bad",
    REVOKED: "s-bad",
    HIGH: "s-bad",
    DEAD: "s-bad",
    ERROR: "s-bad",
    BROKEN: "s-bad",
    DOWN: "s-bad",
    SPOOF_DETECTED: "s-spoof",
    CRITICAL: "s-spoof",
    PENDING: "s-pending",
    PENDING_AUTH: "s-pending",
    NOT_ATTEMPTED: "s-pending",
    CANCELLED: "s-pending",
    EXPIRED: "s-pending",
    INACTIVE: "s-pending",
    ERASED: "s-pending",
    NONE: "s-pending",
    MANUAL_REVIEW: "s-review",
    REVIEW: "s-review",
    OPEN: "s-review",
    SUSPENDED: "s-review",
    MEDIUM: "s-review",
    RETRYING: "s-review",
    WARN: "s-review",
    INSUFFICIENT_DATA: "s-degraded",
    UNAVAILABLE: "s-degraded",
    UNKNOWN: "s-degraded"
  };
  function statusClass(s) {
    if (s === null || s === undefined) return "s-pending";
    const key = String(s).toUpperCase();
    if (STATUS_CLASS[key]) return STATUS_CLASS[key];
    if (key.startsWith("DOWN") || key.startsWith("HTTP_")) return "s-bad";
    return "s-neutral";
  }
  function badge(s, label) {
    return el("span", {
      class: `badge ${statusClass(s)}`
    }, label !== undefined ? label : s === null || s === undefined ? "—" : String(s));
  }
  function chips(list, cls) {
    if (!Array.isArray(list) || !list.length) return el("span", {
      class: "muted",
      text: "—"
    });
    return el("span", {
      class: "chips"
    }, list.map(f => el("span", {
      class: `chip ${cls || "s-neutral"}`,
      text: String(f)
    })));
  }
  function yesNo(v) {
    if (v === null || v === undefined) return "—";
    return v ? "yes" : "no";
  }
  function kvList(obj) {
    if (!obj || typeof obj !== "object") return el("span", {
      class: "muted",
      text: "—"
    });
    const dl = el("dl", {
      class: "kv"
    });
    const entries = Object.entries(obj);
    if (!entries.length) return el("span", {
      class: "muted",
      text: "—"
    });
    for (const [k, v] of entries) {
      let val;
      if (typeof v === "boolean") val = yesNo(v); else if (v === null || v === undefined) val = "—"; else if (typeof v === "object") val = el("code", {
        text: JSON.stringify(v)
      }); else val = String(v);
      dl.appendChild(el("dt", {
        text: k
      }));
      dl.appendChild(el("dd", null, val));
    }
    return dl;
  }
  function table(columns, rows, opts = {}) {
    const wrap = el("div", {
      class: "table-wrap"
    });
    const t = el("table", {
      class: "data"
    });
    if (opts.caption) t.appendChild(el("caption", {
      class: "sr-only",
      text: opts.caption
    }));
    t.appendChild(el("thead", null, el("tr", null, columns.map(c => el("th", {
      scope: "col",
      class: c.num ? "num" : null
    }, c.label)))));
    const tb = el("tbody");
    if (!rows.length) {
      tb.appendChild(el("tr", null, el("td", {
        colspan: String(columns.length),
        class: "empty",
        text: opts.empty || "Nothing to show."
      })));
    }
    for (const r of rows) {
      const tr = el("tr");
      for (const c of columns) {
        const v = c.render(r);
        tr.appendChild(el("td", {
          class: [ c.cls, c.num ? "num" : null ].filter(Boolean).join(" ") || null
        }, v === null || v === undefined || v === "" ? "—" : v));
      }
      if (opts.onRow) opts.onRow(tr, r, tb);
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    wrap.appendChild(t);
    return wrap;
  }
  function can(perm) {
    return Boolean(state.me && Array.isArray(state.me.permissions) && state.me.permissions.includes(perm));
  }
  function currencyDigits(cur) {
    try {
      return new Intl.NumberFormat("en", {
        style: "currency",
        currency: cur
      }).resolvedOptions().maximumFractionDigits;
    } catch (_) {
      return 2;
    }
  }
  function store(key, value) {
    try {
      if (value === null || value === undefined) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value);
    } catch (_) {}
  }
  function load(key) {
    try {
      return sessionStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }
  class ApiError extends Error {
    constructor(status, code, message, fields) {
      super(message || code);
      this.status = status;
      this.code = code;
      this.fields = fields;
    }
  }
  async function api(method, path, body, opts = {}) {
    const url = new URL(API + path, window.location.origin);
    if (opts.tenant !== false && state.tenant) url.searchParams.set("tenant", state.tenant);
    for (const [k, v] of Object.entries(opts.query || {})) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
    const headers = {
      accept: "application/json"
    };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (method !== "GET" && method !== "HEAD") {
      const csrf = load(CSRF_KEY);
      if (csrf) headers["x-csrf-token"] = csrf;
    }
    let res;
    try {
      res = await fetch(url.toString(), {
        method: method,
        headers: headers,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        body: body === undefined ? undefined : JSON.stringify(body)
      });
    } catch (_) {
      throw new ApiError(0, "NETWORK_ERROR", "the server could not be reached");
    }
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const e = data && data.error || {
        code: `HTTP_${res.status}`,
        message: res.statusText || "request failed"
      };
      const err = new ApiError(res.status, e.code, e.message, e.fields);
      if (res.status === 401 && !opts.signin) sessionEnded(err);
      throw err;
    }
    return data;
  }
  function showSignin(notice) {
    stopTimers();
    $("app").hidden = true;
    $("boot").hidden = true;
    $("signin").hidden = false;
    resetTotp();
    $("login-form").hidden = false;
    $("totp-form").hidden = true;
    $("login-password").value = "";
    showError($("login-error"), null);
    const n = $("signin-notice");
    if (notice) {
      n.textContent = notice;
      n.hidden = false;
    } else {
      n.hidden = true;
      n.textContent = "";
    }
    $("login-email").focus();
  }
  function sessionEnded(err) {
    store(CSRF_KEY, null);
    state.me = null;
    state.policy = null;
    state.draft = null;
    state.dirty = false;
    showSignin(`Your session has ended (${err.message}). Sign in again.`);
  }
  function resetTotp() {
    const img = $("totp-qr");
    img.removeAttribute("src");
    $("totp-secret").textContent = "";
    $("totp-setup").hidden = true;
    $("totp-code").value = "";
    showError($("totp-error"), null);
  }
  function svgDataUrl(svg) {
    const bytes = (new TextEncoder).encode(svg);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 32768) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
    }
    return `data:image/svg+xml;base64,${btoa(bin)}`;
  }
  async function onLogin(ev) {
    ev.preventDefault();
    const btn = ev.submitter || ev.target.querySelector("button[type=submit]");
    showError($("login-error"), null);
    btn.disabled = true;
    try {
      const out = await api("POST", "/login", {
        email: $("login-email").value.trim(),
        password: $("login-password").value
      }, {
        tenant: false,
        signin: true
      });
      $("login-password").value = "";
      store(CSRF_KEY, out.csrf);
      resetTotp();
      if (out.stage === "DONE") {
        $("signin").hidden = true;
        await startApp(out.me);
        return;
      }
      if (out.stage === "TOTP_SETUP") {
        $("totp-qr").src = svgDataUrl(String(out.qrSvg || ""));
        $("totp-secret").textContent = String(out.secret || "").replace(/(.{4})/g, "$1 ").trim();
        $("totp-setup").hidden = false;
        $("totp-prompt").textContent = "Then enter the 6-digit code your authenticator shows.";
      } else {
        $("totp-prompt").textContent = "Enter the 6-digit code from your authenticator app.";
      }
      $("login-form").hidden = true;
      $("totp-form").hidden = false;
      $("signin-notice").hidden = true;
      $("totp-code").focus();
    } catch (err) {
      showError($("login-error"), err);
    } finally {
      btn.disabled = false;
    }
  }
  async function onTotp(ev) {
    ev.preventDefault();
    const btn = ev.submitter || ev.target.querySelector("button[type=submit]");
    const code = $("totp-code").value.replace(/\s+/g, "");
    if (!/^\d{6}$/.test(code)) {
      showError($("totp-error"), {
        code: "TOTP_FORMAT",
        message: "enter the 6 digits shown in your authenticator"
      });
      return;
    }
    showError($("totp-error"), null);
    btn.disabled = true;
    try {
      const out = await api("POST", "/login/totp", {
        code: code
      }, {
        tenant: false,
        signin: true
      });
      store(CSRF_KEY, out.csrf);
      resetTotp();
      $("signin").hidden = true;
      await startApp(out.me);
    } catch (err) {
      $("totp-code").value = "";
      if (err.status === 401 && err.code !== "TOTP_INVALID") {
        showSignin(`${err.message}.`);
        return;
      }
      showError($("totp-error"), err);
      $("totp-code").focus();
    } finally {
      btn.disabled = false;
    }
  }
  async function onSignout() {
    try {
      await api("POST", "/logout", undefined, {
        tenant: false,
        signin: true
      });
    } catch (_) {}
    store(CSRF_KEY, null);
    state.me = null;
    state.policy = null;
    state.draft = null;
    state.dirty = false;
    showSignin("You have signed out.");
  }
  async function startApp(me) {
    state.me = me || await api("GET", "/me", undefined, {
      tenant: false
    });
    renderShell();
    $("boot").hidden = true;
    $("signin").hidden = true;
    $("app").hidden = false;
    routeFromHash();
  }
  function renderShell() {
    const me = state.me;
    $("me-email").textContent = me.email;
    $("me-role").textContent = me.role;
    const sel = $("tenant-select");
    const fixed = $("tenant-fixed");
    const tenants = Array.isArray(me.tenants) ? me.tenants : [];
    if (me.tenantId) {
      state.tenant = me.tenantId;
      const t = tenants.find(x => x.id === me.tenantId);
      sel.hidden = true;
      fixed.hidden = false;
      fixed.textContent = t ? t.name : me.tenantId;
    } else {
      sel.hidden = false;
      fixed.hidden = true;
      clear(sel);
      for (const t of tenants) sel.appendChild(el("option", {
        value: t.id,
        text: `${t.name} (${t.id})`
      }));
      const saved = load(TENANT_KEY);
      const pick = tenants.find(t => t.id === saved) || tenants.find(t => t.id === state.tenant) || tenants[0];
      state.tenant = pick ? pick.id : null;
      if (pick) sel.value = pick.id;
      sel.disabled = tenants.length === 0;
    }
    $("no-tenant").hidden = Boolean(state.tenant);
    const tn = tenants.find(t => t.id === state.tenant);
    $("operator-tenant").textContent = tn ? tn.name : state.tenant || "none";
    for (const b of document.querySelectorAll("#nav button[data-view]")) {
      const perms = (b.dataset.perm || "").split(/\s+/).filter(Boolean);
      b.parentElement.hidden = perms.length > 0 && !perms.some(can);
    }
    for (const f of document.querySelectorAll("#view-admin [data-perm]")) {
      f.hidden = !can(f.dataset.perm);
    }
  }
  const VIEWS = {
    overview: {
      load: loadOverview,
      tenant: true
    },
    transactions: {
      load: loadTransactions,
      tenant: true
    },
    decisions: {
      load: loadDecisions,
      tenant: true
    },
    reviews: {
      load: loadReviews,
      tenant: true
    },
    events: {
      load: loadEvents,
      tenant: true
    },
    customers: {
      load: () => {},
      tenant: true
    },
    policy: {
      load: loadPolicy,
      tenant: true
    },
    prompt: {
      load: () => {},
      tenant: true
    },
    integrations: {
      load: loadIntegrations,
      tenant: true
    },
    audit: {
      load: loadAudit,
      tenant: true
    },
    admin: {
      load: () => {},
      tenant: false
    }
  };
  function allowedViews() {
    return [ ...document.querySelectorAll("#nav button[data-view]") ].filter(b => !b.parentElement.hidden).map(b => b.dataset.view);
  }
  function routeFromHash() {
    const want = (window.location.hash || "").replace(/^#\/?/, "");
    const allowed = allowedViews();
    let view = allowed.includes(want) ? want : allowed[0];
    if (!state.tenant && VIEWS[view] && VIEWS[view].tenant && allowed.includes("admin")) view = "admin";
    if (view) showView(view);
  }
  function showView(view) {
    if (state.view === "policy" && view !== "policy" && state.dirty) {
      if (!window.confirm("You have unsaved policy changes. Leave the policy editor anyway? (Your draft is kept until you reload the page or change tenant.)")) {
        window.history.replaceState(null, "", "#/policy");
        return;
      }
    }
    stopTimers();
    state.view = view;
    if (window.location.hash !== `#/${view}`) window.history.replaceState(null, "", `#/${view}`);
    for (const s of document.querySelectorAll("section.view")) s.hidden = s.id !== `view-${view}`;
    for (const b of document.querySelectorAll("#nav button[data-view]")) {
      if (b.dataset.view === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    }
    const def = VIEWS[view];
    if (def.tenant && !state.tenant) {
      $("no-tenant").hidden = false;
      return;
    }
    $("no-tenant").hidden = true;
    Promise.resolve(def.load()).catch(() => {});
  }
  function reload(view) {
    if (view === state.view) Promise.resolve(VIEWS[view].load()).catch(() => {});
  }
  function stopTimers() {
    if (state.txnTimer) {
      clearInterval(state.txnTimer);
      state.txnTimer = null;
    }
  }
  async function onTenantChange() {
    const next = $("tenant-select").value;
    if (state.dirty && !window.confirm("Switching tenant discards your unsaved policy changes. Continue?")) {
      $("tenant-select").value = state.tenant;
      return;
    }
    state.tenant = next;
    store(TENANT_KEY, next);
    state.policy = null;
    state.draft = null;
    state.dirty = false;
    state.keysScopes = null;
    clear($("customers-body")).appendChild(el("p", {
      class: "muted",
      text: "Search by the bank's own customer reference."
    }));
    clear($("prompt-body"));
    clear($("secret-slot"));
    clear($("audit-verify-result"));
    renderShell();
    showView(state.view || "overview");
  }
  async function withBody(body, fn) {
    if (!body.firstChild) body.appendChild(loading());
    try {
      await fn();
    } catch (err) {
      if (err.status === 401) return;
      replace(body, errorBox(err));
    }
  }
  function kpi(label, value, sub, cls) {
    return el("div", {
      class: `kpi ${cls || ""}`
    }, el("div", {
      class: "kpi-label",
      text: label
    }), el("div", {
      class: "kpi-value",
      text: String(value)
    }), sub ? el("div", {
      class: "kpi-sub",
      text: sub
    }) : null);
  }
  function bars(obj, order, clsFor) {
    const entries = Object.entries(obj || {});
    if (order) entries.sort((a, b) => {
      const ia = order.indexOf(a[0]);
      const ib = order.indexOf(b[0]);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    if (!entries.length) return el("p", {
      class: "muted",
      text: "No data in this window."
    });
    const max = Math.max(1, ...entries.map(([, v]) => Number(v) || 0));
    const total = entries.reduce((s, [, v]) => s + (Number(v) || 0), 0);
    return el("div", {
      class: "bars"
    }, entries.map(([k, v]) => {
      const fill = el("div", {
        class: `bar-fill ${clsFor ? clsFor(k) : ""}`
      });
      fill.style.width = `${Math.round((Number(v) || 0) / max * 100)}%`;
      const pct = total ? Math.round((Number(v) || 0) / total * 100) : 0;
      return el("div", {
        class: "bar-row"
      }, el("span", {
        class: "bar-label",
        title: k,
        text: k
      }), el("div", {
        class: "bar-track",
        role: "img",
        "aria-label": `${k}: ${v} (${pct}%)`
      }, fill), el("span", {
        class: "bar-value",
        text: String(v)
      }));
    }));
  }
  async function loadOverview() {
    const body = $("overview-body");
    await withBody(body, async () => {
      const o = await api("GET", "/overview");
      const tx = o.transactions || {};
      const txTotal = Object.values(tx).reduce((s, n) => s + (Number(n) || 0), 0);
      const src = o.decisionSource || {};
      const tiles = el("div", {
        class: "kpis"
      }, kpi("Transactions", txTotal, `in ${o.windowHours || 24} h`), Object.keys(tx).sort().map(k => kpi(k.replace(/_/g, " ").toLowerCase(), tx[k], "transactions", statusClass(k) === "s-bad" ? "alert" : statusClass(k) === "s-review" ? "warn" : "")), kpi("Open reviews", o.openReviews, "manual review queue", o.openReviews ? "warn" : ""), kpi("High-severity events", o.highSeverityEvents, `in ${o.windowHours || 24} h`, o.highSeverityEvents ? "alert" : ""), kpi("Customers", o.customers, "not erased"), kpi("Avg decision", `${o.avgDecisionMs} ms`, "engine latency"));
      const fo = o.factorOutcomes || {};
      const statuses = [ ...new Set(Object.values(fo).flatMap(m => Object.keys(m))) ].sort();
      const foRows = Object.keys(fo).sort().map(f => ({
        factor: f,
        counts: fo[f]
      }));
      const foTable = statuses.length ? table([ {
        label: "Factor",
        render: r => el("strong", {
          text: r.factor
        })
      }, ...statuses.map(s => ({
        label: s,
        num: true,
        render: r => {
          const n = r.counts[s];
          return n ? badge(s, String(n)) : el("span", {
            class: "muted",
            text: "0"
          });
        }
      })) ], foRows, {
        caption: "Factor outcomes by status"
      }) : el("p", {
        class: "muted",
        text: "No factor attempts in this window."
      });
      const svc = o.factorServices || {};
      const svcList = el("dl", {
        class: "kv"
      }, Object.keys(svc).sort().map(k => [ el("dt", {
        text: k
      }), el("dd", null, badge(String(svc[k]).toLowerCase() === "up" ? "UP" : String(svc[k]).startsWith("down") ? "DOWN" : svc[k], String(svc[k]))) ]));
      const ag = o.agent || {};
      const agentList = kvList({
        requested: ag.requested,
        active: ag.active,
        timeoutS: ag.timeoutS,
        error: ag.error || "none"
      });
      replace(body, tiles, el("div", {
        class: "overview-grid"
      }, el("div", {
        class: "card"
      }, el("h3", {
        text: "Risk bands"
      }), bars(o.riskBands, [ "LOW", "MEDIUM", "HIGH" ], statusClass)), el("div", {
        class: "card"
      }, el("h3", {
        text: "Decision source"
      }), bars(src, [ "agent", "rules" ]), el("p", {
        class: "hint",
        text: "agent = AI-selected plan within policy bounds; rules = deterministic rule table (fallback or agent disabled)."
      })), el("div", {
        class: "card"
      }, el("h3", {
        text: "Transactions by status"
      }), bars(tx, null, statusClass)), el("div", {
        class: "card"
      }, el("h3", {
        text: "Factor service health"
      }), svcList), el("div", {
        class: "card"
      }, el("h3", {
        text: "Agent backend"
      }), agentList)), el("div", {
        class: "card card-gap"
      }, el("h3", {
        text: "Factor outcomes"
      }), foTable));
    });
  }
  function stepChips(steps) {
    if (!Array.isArray(steps) || !steps.length) return el("span", {
      class: "muted",
      text: "—"
    });
    return el("span", {
      class: "chips"
    }, steps.map(s => {
      const added = s.addedBy && s.addedBy !== "plan" ? s.addedBy : null;
      const title = `${s.factor}: ${s.status}, ${s.attempts} attempt(s)${added ? `, added by ${added}` : ""}`;
      return el("span", {
        class: `chip ${statusClass(s.status)}${added ? ` added-${added}` : ""}`,
        title: title,
        "aria-label": title
      }, s.factor, added ? el("span", {
        class: "chip-mark",
        text: added === "escalation" ? "esc" : added === "substitution" ? "sub" : added
      }) : null);
    }));
  }
  function attemptList(list) {
    const bad = (list || []).filter(a => a.status !== "PASSED");
    if (!bad.length) return null;
    return el("ul", {
      class: "plain-list attempt-list"
    }, bad.map(a => {
      const bits = [ `${a.factor} ${a.status}${a.reason ? `: ${a.reason}` : ""}` ];
      if (a.score != null && a.threshold != null) bits.push(`score ${a.score} (needs ${a.threshold})`);
      if (a.liveness != null) bits.push(`liveness ${a.liveness}${a.pad ? ` ${a.pad}` : ""}`);
      const d = a.detail || {};
      if (Array.isArray(d.yaws)) {
        const dir = d.action === "TURN_LEFT" ? "left (+)" : "right (−)";
        bits.push(`head angle per frame [${d.yaws.join(", ")}]; asked to turn ${dir}; ` + `needs a frame within ±${d.needFrontalMax} then a change of ${d.needTurnDelta}; ` + `frontal frames ${d.frontalFrames}, turned frames ${d.turnedFrames}`);
      }
      return el("li", {
        class: "muted",
        text: bits.join(" · ")
      });
    }));
  }
  function escalationList(list) {
    if (!Array.isArray(list) || !list.length) return el("span", {
      class: "muted",
      text: "—"
    });
    return el("ul", {
      class: "plain-list"
    }, list.map(e => el("li", null, e && e.reason ? String(e.reason) : JSON.stringify(e), e && Array.isArray(e.appended) && e.appended.length ? el("span", {
      class: "muted",
      text: ` (+${e.appended.join(", ")})`
    }) : null)));
  }
  async function loadTransactions() {
    const body = $("transactions-body");
    await withBody(body, async () => {
      const out = await api("GET", "/transactions", undefined, {
        query: {
          limit: $("txn-limit").value
        }
      });
      replace(body, table([ {
        label: "ID",
        cls: "id",
        render: r => r.id
      }, {
        label: "Customer",
        cls: "nowrap",
        render: r => r.customerRef || r.customerId
      }, {
        label: "External ID",
        cls: "id",
        render: r => r.externalId
      }, {
        label: "Type",
        render: r => r.type
      }, {
        label: "Amount",
        num: true,
        cls: "nowrap",
        render: r => r.amount
      }, {
        label: "Status",
        render: r => [ badge(r.status), r.resolution ? el("span", {
          class: "sub-status",
          text: r.resolution
        }) : null ]
      }, {
        label: "Risk",
        render: r => r.riskBand ? badge(r.riskBand) : null
      }, {
        label: "Steps",
        render: r => [ stepChips(r.steps), attemptList(r.attempts) ]
      }, {
        label: "Escalations",
        render: r => escalationList(r.escalations)
      }, {
        label: "Channel",
        render: r => r.channel
      }, {
        label: "Created",
        render: r => timeCell(r.createdAt)
      }, {
        label: "",
        render: r => el("button", {
          type: "button",
          class: "btn btn-sm",
          text: "Decision",
          "aria-label": `Show decision for ${r.id}`,
          onclick: () => openDecisionFor(r.id)
        })
      } ], out.transactions || [], {
        empty: "No transactions yet.",
        caption: "Recent transactions"
      }));
      $("txn-updated").textContent = `Updated ${(new Date).toLocaleTimeString()}`;
    });
    armTxnTimer();
  }
  function armTxnTimer() {
    stopTimers();
    if (state.view === "transactions" && $("txn-auto").checked) {
      state.txnTimer = setInterval(() => {
        if (state.view === "transactions" && !document.hidden) loadTransactions();
      }, REFRESH_MS);
    }
  }
  function openDecisionFor(txnId) {
    state.decisionFilter = txnId;
    $("decision-txn").value = txnId;
    showView("decisions");
  }
  function decisionDetail(d) {
    const ctx = d.context || {};
    const derived = ctx._derived || {};
    const typed = Object.assign({}, ctx);
    delete typed._derived;
    const block = (title, ...content) => el("div", {
      class: "detail-block"
    }, el("h4", {
      text: title
    }), ...content);
    return el("div", null, el("div", {
      class: "detail-grid"
    }, block("Reasoning", el("p", {
      class: "prose",
      text: d.reasoning || "—"
    })), block("Counterfactual", el("p", {
      class: "prose",
      text: d.counterfactual || "—"
    })), block("Factors", el("dl", {
      class: "kv"
    }, el("dt", {
      text: "Rules baseline"
    }), el("dd", null, chips(d.rulesBaseline)), el("dt", {
      text: "Final required"
    }), el("dd", null, chips(d.required, "s-ok")), el("dt", {
      text: "Agent added"
    }), el("dd", null, chips(d.agentAdded, "s-review")), el("dt", {
      text: "Org policy added"
    }), el("dd", null, chips(d.orgAdded, "s-neutral")))), block("Decision", kvList({
      floor: d.floor,
      floorApplied: d.floorApplied,
      source: d.source,
      agent: d.agent || "—",
      agentError: d.agentError || "none",
      escalate: d.escalate || "none",
      latencyMs: d.latencyMs,
      policyVersion: d.policyVersion,
      planId: d.planId,
      customerId: d.customerId
    }))), el("h4", {
      class: "detail-sub",
      text: "Evidence behind the context flags"
    }), el("div", {
      class: "detail-grid"
    }, [ "device", "location", "hour", "payee" ].map(k => block(k, kvList(derived[k]))), Object.keys(derived).filter(k => ![ "device", "location", "hour", "payee" ].includes(k)).map(k => block(k, kvList(derived[k])))), el("details", null, el("summary", {
      text: "Typed context the engine saw"
    }), el("pre", {
      class: "block",
      text: JSON.stringify(typed, null, 2)
    })), d.preview ? el("details", null, el("summary", {
      text: "Behavioural preview (typed signals only)"
    }), el("pre", {
      class: "block",
      text: JSON.stringify(d.preview, null, 2)
    })) : null);
  }
  async function loadDecisions() {
    const body = $("decisions-body");
    await withBody(body, async () => {
      const out = await api("GET", "/decisions", undefined, {
        query: {
          transactionId: state.decisionFilter,
          limit: 100
        }
      });
      const cols = [ {
        label: "",
        render: r => el("button", {
          type: "button",
          class: "expander",
          "aria-expanded": "false",
          "aria-label": `Expand decision ${r.planId}`,
          text: "▸",
          dataset: {
            role: "expander"
          }
        })
      }, {
        label: "Decided",
        render: r => timeCell(r.decidedAt)
      }, {
        label: "Transaction",
        cls: "id",
        render: r => r.transactionId
      }, {
        label: "Risk",
        render: r => badge(r.riskBand)
      }, {
        label: "Floor",
        render: r => `${r.floor}${r.floorApplied ? " (applied)" : ""}`
      }, {
        label: "Source",
        render: r => r.source
      }, {
        label: "Required",
        render: r => chips(r.required, "s-ok")
      }, {
        label: "Escalate",
        render: r => r.escalate ? badge(r.escalate) : null
      }, {
        label: "Latency",
        num: true,
        render: r => `${r.latencyMs} ms`
      } ];
      replace(body, table(cols, out.decisions || [], {
        empty: state.decisionFilter ? "No decision for that transaction." : "No decisions yet.",
        caption: "Engine decisions",
        onRow: (tr, r) => {
          tr.classList.add("clickable");
          let detail = null;
          const toggle = () => {
            const btn = tr.querySelector("[data-role=expander]");
            if (detail) {
              detail.remove();
              detail = null;
              tr.classList.remove("expanded");
              btn.setAttribute("aria-expanded", "false");
              btn.textContent = "▸";
              return;
            }
            detail = el("tr", {
              class: "detail-row"
            }, el("td", {
              colspan: String(cols.length)
            }, decisionDetail(r)));
            tr.after(detail);
            tr.classList.add("expanded");
            btn.setAttribute("aria-expanded", "true");
            btn.textContent = "▾";
          };
          tr.addEventListener("click", ev => {
            if (window.getSelection && String(window.getSelection()).length && ev.target.tagName !== "BUTTON") return;
            toggle();
          });
        }
      }));
    });
  }
  function reviewCard(r) {
    const card = el("article", {
      class: "card review",
      "aria-label": `Review case ${r.id}`
    }, el("div", {
      class: "review-head"
    }, badge(r.status), el("strong", {
      text: r.type || "transaction"
    }), el("span", {
      text: r.amount || ""
    }), el("span", {
      class: "id",
      text: r.id
    })), el("dl", {
      class: "kv"
    }, el("dt", {
      text: "Reason"
    }), el("dd", {
      class: "prose",
      text: r.reason || "—"
    }), el("dt", {
      text: "Transaction"
    }), el("dd", null, el("code", {
      text: r.transactionId || "—"
    })), el("dt", {
      text: "Customer"
    }), el("dd", null, el("code", {
      text: r.customer || "—"
    }), " ", r.customerLocked ? badge("LOCKED") : null), el("dt", {
      text: "Opened"
    }), el("dd", null, timeCell(r.createdAt)), r.resolvedBy ? [ el("dt", {
      text: "Resolved by"
    }), el("dd", null, el("code", {
      text: r.resolvedBy
    })) ] : null, r.note ? [ el("dt", {
      text: "Note"
    }), el("dd", {
      class: "prose",
      text: r.note
    }) ] : null));
    if (r.customerLocked) {
      card.appendChild(el("p", {
        class: "warn-box"
      }, el("strong", {
        text: "This customer is locked. "
      }), "If the lock was caused by a detected presentation attack (a spoofed face or voice), approving this case records your decision but the server will not issue an authorisation proof, and the transaction stays unauthorised. Investigate the security events for this customer first."));
    }
    if (r.status === "OPEN" && can("review:decide")) {
      const noteId = `note-${r.id}`;
      const note = el("textarea", {
        id: noteId,
        rows: "2",
        maxlength: "500",
        required: true
      });
      const msg = el("div", {
        role: "alert"
      });
      const approve = el("button", {
        type: "button",
        class: "btn btn-ok",
        text: "Approve",
        disabled: true
      });
      const reject = el("button", {
        type: "button",
        class: "btn btn-danger",
        text: "Reject",
        disabled: true
      });
      const counter = el("span", {
        class: "hint",
        text: "Note required: at least 10 characters."
      });
      note.addEventListener("input", () => {
        const n = note.value.trim().length;
        approve.disabled = reject.disabled = n < 10;
        counter.textContent = n < 10 ? `Note required: at least 10 characters (${n}/10).` : `${n}/500 characters.`;
      });
      const decide = async ok => {
        const text = note.value.trim();
        if (text.length < 10) return;
        if (!window.confirm(`${ok ? "Approve" : "Reject"} case ${r.id}? This is recorded in the audit log.`)) return;
        approve.disabled = reject.disabled = true;
        clear(msg);
        try {
          const res = await api("POST", `/reviews/${encodeURIComponent(r.id)}/decide`, {
            approve: ok,
            note: text
          }, {
            tenant: false
          });
          let text2 = `Case ${res.status}.`;
          if (ok) text2 += res.proofIssued ? " An authorisation proof was issued." : " No proof was issued (customer locked after a presentation attack, or the transaction is no longer pending).";
          replace(msg, el("p", {
            class: res.proofIssued || !ok ? "success" : "warn-box",
            text: text2
          }));
          note.disabled = true;
          setTimeout(() => {
            if (state.view === "reviews") loadReviews();
          }, 2500);
        } catch (err) {
          replace(msg, errorBox(err));
          approve.disabled = reject.disabled = note.value.trim().length < 10;
        }
      };
      approve.addEventListener("click", () => decide(true));
      reject.addEventListener("click", () => decide(false));
      card.appendChild(el("div", {
        class: "review-actions"
      }, el("div", {
        class: "field"
      }, el("label", {
        for: noteId,
        text: "Decision note (recorded in the audit log)"
      }), note, counter), el("div", {
        class: "row"
      }, approve, reject), msg));
    }
    return card;
  }
  async function loadReviews() {
    const body = $("reviews-body");
    await withBody(body, async () => {
      const out = await api("GET", "/reviews", undefined, {
        query: {
          status: $("review-status").value
        }
      });
      const list = out.reviews || [];
      replace(body, list.length ? el("div", {
        class: "review-list"
      }, list.map(reviewCard)) : el("p", {
        class: "empty",
        text: "No cases with this status."
      }));
    });
  }
  async function loadEvents() {
    const body = $("events-body");
    await withBody(body, async () => {
      const out = await api("GET", "/events", undefined, {
        query: {
          limit: 200
        }
      });
      replace(body, table([ {
        label: "When",
        render: r => timeCell(r.at)
      }, {
        label: "Severity",
        render: r => badge(r.severity)
      }, {
        label: "Kind",
        cls: "nowrap",
        render: r => el("code", {
          text: r.kind
        })
      }, {
        label: "Message",
        cls: "wrap-text",
        render: r => r.message
      }, {
        label: "Session",
        cls: "id",
        render: r => r.sessionId
      }, {
        label: "Customer",
        cls: "id",
        render: r => r.customerId
      } ], out.events || [], {
        empty: "No security events.",
        caption: "Security events"
      }));
    });
  }
  async function onCustomerSearch(ev) {
    ev.preventDefault();
    const ref = $("customer-ref").value.trim();
    if (!ref) return;
    const body = $("customers-body");
    replace(body, loading());
    try {
      const c = await api("GET", "/customers", undefined, {
        query: {
          externalRef: ref
        }
      });
      replace(body, customerCard(c));
    } catch (err) {
      if (err.status !== 401) replace(body, errorBox(err));
    }
  }
  function customerCard(c) {
    const card = el("div", {
      class: "card"
    }, el("div", {
      class: "review-head"
    }, el("h3", {
      text: c.externalRef
    }), badge(c.status), el("span", {
      class: "id",
      text: c.id
    })), el("div", {
      class: "grid-3"
    }, el("div", null, el("h4", {
      text: "Account"
    }), kvList({
      status: c.status,
      lockedReason: c.lockedReason || "—",
      segment: c.segment,
      verifiedTransactions: c.verifiedTransactions,
      devices: c.devices
    })), el("div", null, el("h4", {
      text: "Enrollment"
    }), Object.keys(c.enrollment || {}).length ? el("dl", {
      class: "kv"
    }, Object.entries(c.enrollment).map(([f, s]) => [ el("dt", {
      text: f
    }), el("dd", null, badge(s)) ])) : el("p", {
      class: "muted",
      text: "Not enrolled in any factor."
    })), el("div", null, el("h4", {
      text: "Consent"
    }), c.consent ? kvList({
      version: c.consent.version,
      at: fmtTime(c.consent.at)
    }) : el("p", {
      class: "muted",
      text: "No consent recorded."
    }))));
    if (can("customers:manage")) {
      const actionSel = el("select", {
        id: "cust-action"
      }, el("option", {
        value: "unlock",
        text: "Unlock"
      }), el("option", {
        value: "suspend",
        text: "Suspend"
      }), el("option", {
        value: "segment",
        text: "Change segment"
      }));
      const segSel = el("select", {
        id: "cust-segment",
        disabled: true
      }, SEGMENTS.map(s => el("option", {
        value: s,
        text: s
      })));
      segSel.value = SEGMENTS.includes(c.segment) ? c.segment : "STANDARD";
      actionSel.addEventListener("change", () => {
        segSel.disabled = actionSel.value !== "segment";
      });
      const note = el("textarea", {
        id: "cust-note",
        rows: "2",
        maxlength: "300",
        required: true,
        minlength: "10"
      });
      const msg = el("div", {
        role: "alert"
      });
      const submit = el("button", {
        type: "submit",
        class: "btn btn-primary",
        text: "Apply"
      });
      const form = el("form", {
        class: "subform"
      }, el("h4", {
        text: "Operator action"
      }), el("div", {
        class: "grid-3"
      }, el("div", {
        class: "field"
      }, el("label", {
        for: "cust-action",
        text: "Action"
      }), actionSel), el("div", {
        class: "field"
      }, el("label", {
        for: "cust-segment",
        text: "New segment"
      }), segSel)), el("div", {
        class: "field"
      }, el("label", {
        for: "cust-note",
        text: "Reason (at least 10 characters, recorded in the audit log)"
      }), note), msg, submit);
      if (c.status === "LOCKED" && c.lockedReason === "PRESENTATION_ATTACK") {
        form.insertBefore(el("p", {
          class: "danger-box"
        }, el("strong", {
          text: "Locked after a presentation attack. "
        }), "Unlock only after the bank has re-verified the customer's identity out of band."), form.firstChild.nextSibling);
      }
      form.addEventListener("submit", async ev => {
        ev.preventDefault();
        const text = note.value.trim();
        if (text.length < 10) {
          replace(msg, errorBox({
            code: "NOTE_REQUIRED",
            message: "the note must be at least 10 characters"
          }));
          return;
        }
        const payload = {
          action: actionSel.value,
          note: text
        };
        if (payload.action === "segment") payload.segment = segSel.value;
        if (!window.confirm(`Apply "${actionSel.options[actionSel.selectedIndex].text}" to ${c.externalRef}?`)) return;
        submit.disabled = true;
        clear(msg);
        try {
          await api("POST", `/customers/${encodeURIComponent(c.id)}`, payload, {
            tenant: false
          });
          const fresh = await api("GET", "/customers", undefined, {
            query: {
              externalRef: c.externalRef
            }
          });
          const next = customerCard(fresh);
          card.replaceWith(next);
          next.querySelector("[role=alert]") && replace(next.querySelector("[role=alert]"), el("p", {
            class: "success",
            text: "Action applied."
          }));
        } catch (err) {
          replace(msg, errorBox(err));
          submit.disabled = false;
        }
      });
      card.appendChild(form);
    }
    return card;
  }
  const clone = o => JSON.parse(JSON.stringify(o));
  async function loadPolicy(force) {
    const status = $("policy-status");
    clear($("policy-result"));
    if (state.draft && state.dirty && !force) {
      renderPolicyMeta();
      return;
    }
    replace(status, loading());
    try {
      state.policy = await api("GET", "/policy");
      state.draft = clone(state.policy.policy);
      state.dirty = false;
      clear(status);
      renderPolicyMeta();
      policyToForm(state.draft);
      writeJson();
      markDirty(false);
    } catch (err) {
      if (err.status !== 401) replace(status, errorBox(err));
    }
  }
  function renderPolicyMeta() {
    const p = state.policy;
    if (!p) return;
    $("policy-version").textContent = String(p.version);
    const hist = $("policy-history");
    replace(hist, (p.history || []).length ? el("ol", {
      class: "history-list"
    }, p.history.map(h => el("li", null, el("strong", {
      text: `v${h.version}`
    }), " ", el("span", {
      class: "muted",
      text: fmtTime(h.at)
    }), el("div", {
      class: "small id",
      text: h.by || ""
    })))) : el("p", {
      class: "muted",
      text: "No history."
    }));
    const writable = can("policy:write");
    $("policy-readonly").hidden = writable;
    for (const n of $("policy-form").querySelectorAll("input, select, textarea, button")) {
      n.disabled = !writable;
    }
    $("pol-json").disabled = false;
    $("pol-json").readOnly = !writable;
  }
  function num(v, fallback) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }
  function policyToForm(p) {
    const setv = (id, v) => {
      $(id).value = v === undefined || v === null ? "" : String(v);
    };
    setv("pol-regulatoryProfile", p.regulatoryProfile);
    setv("pol-riskAppetite", p.riskAppetite);
    setv("pol-defaultFloor", p.defaultFloor);
    setv("pol-coldStart", p.coldStartBehaviouralBand);
    $("pol-reviewOnAnyUnmet").checked = Boolean(p.reviewOnAnyUnmet);
    const cur = p.currency || "INR";
    const digits = currencyDigits(cur);
    $("pol-currency-label").textContent = cur;
    const ex = p.lowValueExemption || {};
    $("pol-exempt-enabled").checked = Boolean(ex.enabled);
    $("pol-exempt-max").step = digits ? String(1 / 10 ** digits) : "1";
    setv("pol-exempt-max", ex.maxAmountMinor === undefined ? "" : (num(ex.maxAmountMinor, 0) / 10 ** digits).toFixed(digits));
    updateMinorHint(p);
    const pf = Array.isArray(p.permittedFactors) ? p.permittedFactors : [];
    for (const f of FACTORS) $(`pol-pf-${f}`).checked = pf.includes(f);
    const pad = p.padModes || {};
    setv("pol-pad-FACE", pad.FACE || "advisory");
    setv("pol-pad-VOICE", pad.VOICE || "advisory");
    const ab = p.attemptBudget || {};
    for (const f of FACTORS) setv(`pol-ab-${f}`, ab[f]);
    for (const n of $("policy-form").querySelectorAll("[data-int][data-path]")) setv(n.id, p[n.dataset.path]);
    const qh = p.quietHours || {};
    setv("pol-quiet-start", qh.start);
    setv("pol-quiet-end", qh.end);
    const ag = p.agent || {};
    $("pol-agent-enabled").checked = ag.enabled !== false;
    setv("pol-agent-max", ag.maxAdditions);
    $("pol-agent-guidance").value = ag.guidance || "";
    $("guidance-count").textContent = String(($("pol-agent-guidance").value || "").length);
    $("pol-origins").value = (p.allowedEmbedOrigins || []).join("\n");
    updatePadWarning();
  }
  function updateMinorHint(p) {
    const ex = p && p.lowValueExemption || {};
    $("pol-exempt-minor").textContent = `Stored as ${num(ex.maxAmountMinor, 0)} minor units.`;
  }
  function updatePadWarning() {
    const terminal = $("pol-pad-FACE").value === "terminal" || $("pol-pad-VOICE").value === "terminal";
    $("pad-warning").className = terminal ? "danger-box" : "warn-box";
  }
  function formToDraft() {
    const p = state.draft || {};
    const str = id => $(id).value;
    const int = (id, cur) => {
      const raw = $(id).value.trim();
      if (raw === "") return cur;
      const n = Number(raw);
      return Number.isFinite(n) ? Math.trunc(n) : cur;
    };
    p.regulatoryProfile = str("pol-regulatoryProfile");
    p.riskAppetite = str("pol-riskAppetite");
    p.defaultFloor = str("pol-defaultFloor");
    p.coldStartBehaviouralBand = str("pol-coldStart");
    p.reviewOnAnyUnmet = $("pol-reviewOnAnyUnmet").checked;
    const digits = currencyDigits(p.currency || "INR");
    p.lowValueExemption = Object.assign({}, p.lowValueExemption || {});
    p.lowValueExemption.enabled = $("pol-exempt-enabled").checked;
    const major = $("pol-exempt-max").value.trim();
    if (major !== "" && Number.isFinite(Number(major))) {
      p.lowValueExemption.maxAmountMinor = Math.round(Number(major) * 10 ** digits);
    }
    const pf = [];
    for (const f of FACTORS) if ($(`pol-pf-${f}`).checked) pf.push(f);
    p.permittedFactors = pf;
    p.padModes = Object.assign({}, p.padModes || {}, {
      FACE: str("pol-pad-FACE"),
      VOICE: str("pol-pad-VOICE")
    });
    const ab = Object.assign({}, p.attemptBudget || {});
    for (const f of FACTORS) {
      const v = int(`pol-ab-${f}`, ab[f]);
      if (v !== undefined) ab[f] = v;
    }
    p.attemptBudget = ab;
    for (const n of $("policy-form").querySelectorAll("[data-int][data-path]")) {
      const v = int(n.id, p[n.dataset.path]);
      if (v !== undefined) p[n.dataset.path] = v;
    }
    p.quietHours = Object.assign({}, p.quietHours || {});
    const qs = int("pol-quiet-start", p.quietHours.start);
    const qe = int("pol-quiet-end", p.quietHours.end);
    if (qs !== undefined) p.quietHours.start = qs;
    if (qe !== undefined) p.quietHours.end = qe;
    p.agent = Object.assign({}, p.agent || {});
    p.agent.enabled = $("pol-agent-enabled").checked;
    const am = int("pol-agent-max", p.agent.maxAdditions);
    if (am !== undefined) p.agent.maxAdditions = am;
    const g = $("pol-agent-guidance").value.trim();
    p.agent.guidance = g === "" ? null : g;
    p.allowedEmbedOrigins = $("pol-origins").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    state.draft = p;
  }
  function writeJson() {
    $("pol-json").value = JSON.stringify(state.draft, null, 2);
    showError($("pol-json-error"), null);
    $("pol-json").removeAttribute("aria-invalid");
  }
  function markDirty(d) {
    state.dirty = d;
    $("policy-dirty").textContent = d ? "Unsaved changes" : "";
  }
  function onPolicyFormInput(ev) {
    if (!state.draft || ev.target.id === "pol-json") return;
    if (ev.target.id === "pol-agent-guidance") $("guidance-count").textContent = String(ev.target.value.length);
    if (ev.target.dataset && ev.target.dataset.pad) updatePadWarning();
    formToDraft();
    updateMinorHint(state.draft);
    writeJson();
    markDirty(true);
  }
  function onPolicyJsonInput() {
    const ta = $("pol-json");
    let parsed;
    try {
      parsed = JSON.parse(ta.value);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("the policy must be a JSON object");
    } catch (e) {
      ta.setAttribute("aria-invalid", "true");
      const err = $("pol-json-error");
      err.textContent = `Invalid JSON: ${e.message}. The form is not updated until this parses.`;
      err.hidden = false;
      $("policy-save").disabled = true;
      markDirty(true);
      return;
    }
    ta.removeAttribute("aria-invalid");
    showError($("pol-json-error"), null);
    $("policy-save").disabled = !can("policy:write");
    state.draft = parsed;
    policyToForm(parsed);
    markDirty(true);
  }
  function policyResult(node) {
    replace($("policy-result"), node);
  }
  async function onPolicySave(ev) {
    ev.preventDefault();
    if (!can("policy:write") || !state.policy) return;
    if ($("pol-json").getAttribute("aria-invalid") === "true") {
      policyResult(errorBox({
        code: "JSON_INVALID",
        message: "fix the Advanced JSON before saving"
      }));
      return;
    }
    if (!$("policy-form").checkValidity()) {
      $("policy-form").reportValidity();
      policyResult(errorBox({
        code: "FORM_INVALID",
        message: "a field is outside its permitted range; see the highlighted field"
      }));
      return;
    }
    const pad = state.draft.padModes || {};
    if ((pad.FACE === "terminal" || pad.VOICE === "terminal") && !window.confirm("A presentation-attack detector is set to TERMINAL. Genuine customers flagged as spoofs will be blocked outright. Confirm that the detector's false-rejection rate has been validated for your customer population.")) {
      return;
    }
    const btn = $("policy-save");
    btn.disabled = true;
    policyResult(el("p", {
      class: "muted",
      role: "status",
      text: "Saving…"
    }));
    try {
      const out = await api("PUT", "/policy", {
        expectedVersion: state.policy.version,
        policy: state.draft
      });
      markDirty(false);
      await loadPolicy(true);
      policyResult(el("p", {
        class: "success"
      }, el("strong", {
        text: `Saved as version ${out.version}. `
      }), out.changed && out.changed.length ? `Changed: ${out.changed.join(", ")}.` : "No fields changed."));
    } catch (err) {
      if (err.status === 401) return;
      if (err.status === 409) {
        const reloadBtn = el("button", {
          type: "button",
          class: "btn btn-sm",
          text: "Load latest version (discards your draft)",
          onclick: () => {
            markDirty(false);
            loadPolicy(true);
          }
        });
        policyResult(el("div", {
          class: "danger-box",
          role: "alert"
        }, el("strong", {
          text: "Version conflict. "
        }), `Someone else saved the policy while you were editing (${err.message}; you started from version ${state.policy.version}). Your draft is still here. Reload the latest version, then re-apply your change.`, el("div", {
          class: "row"
        }, reloadBtn)));
      } else if (err.code === "POLICY_INVALID") {
        policyResult(el("div", {
          class: "danger-box",
          role: "alert"
        }, el("strong", {
          text: "The server rejected this policy (POLICY_INVALID)."
        }), el("pre", {
          class: "block",
          text: err.message
        })));
      } else {
        policyResult(errorBox(err));
      }
    } finally {
      btn.disabled = !can("policy:write");
    }
  }
  function onPolicyDefaults() {
    if (!state.policy || !state.policy.defaults) return;
    if (!window.confirm("Load the platform default policy into the editor? Nothing is saved until you press Save.")) return;
    state.draft = clone(state.policy.defaults);
    policyToForm(state.draft);
    writeJson();
    markDirty(true);
    policyResult(el("p", {
      class: "notice",
      text: "Platform defaults loaded into the editor. Review, then Save to apply."
    }));
  }
  function onPolicyRevert() {
    if (!state.policy) return;
    state.draft = clone(state.policy.policy);
    policyToForm(state.draft);
    writeJson();
    markDirty(false);
    $("policy-save").disabled = !can("policy:write");
    clear($("policy-result"));
  }
  async function onPromptPreview(ev) {
    ev.preventDefault();
    const body = $("prompt-body");
    replace(body, loading());
    const payload = {
      amountBand: $("pp-amountBand").value,
      type: $("pp-type").value,
      isNewBeneficiary: $("pp-isNewBeneficiary").checked,
      deviceKnown: $("pp-deviceKnown").checked,
      locationKnown: $("pp-locationKnown").checked,
      hourUnusual: $("pp-hourUnusual").checked,
      behaviouralBand: $("pp-behaviouralBand").value,
      segment: $("pp-segment").value,
      payeeName: $("pp-payeeName").value || null,
      remarks: $("pp-remarks").value || null
    };
    try {
      const out = await api("POST", "/prompt-preview", payload);
      const leaked = Number(out.freeTextReachingModel) || 0;
      const ag = out.agent || {};
      replace(body, el("div", {
        class: `result-banner ${leaked === 0 ? "s-ok" : "s-bad"}`,
        role: "status"
      }, `Free-text fields reaching the model: ${leaked} of ${out.submittedFreeText}`, el("span", {
        class: "sub",
        text: leaked === 0 ? "Prompt injection is impossible by construction: the typed context has no field that could carry payee names or remarks." : "Unexpected: free text appears in the rendered prompt. Report this to the platform team."
      })), el("div", {
        class: "card"
      }, kvList({
        floor: out.floor,
        agentRequested: ag.requested,
        agentActive: ag.active,
        agentError: ag.error || "none"
      })), el("h3", {
        text: "System prompt"
      }), el("pre", {
        class: "block",
        text: out.system
      }), el("h3", {
        text: "Rendered prompt (exact bytes sent to the model)"
      }), el("pre", {
        class: "block",
        text: out.prompt
      }));
    } catch (err) {
      if (err.status !== 401) replace(body, errorBox(err));
    }
  }
  function showSecretOnce(title, id, secret, note) {
    const slot = $("secret-slot");
    const value = el("code", {
      class: "secret-value",
      text: secret
    });
    const status = el("span", {
      class: "hint",
      "aria-live": "polite"
    });
    const copy = el("button", {
      type: "button",
      class: "btn btn-sm btn-primary",
      text: "Copy"
    });
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(secret);
        status.textContent = "Copied to clipboard.";
      } catch (_) {
        const r = document.createRange();
        r.selectNodeContents(value);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
        status.textContent = "Clipboard unavailable: the secret is selected; press Ctrl+C / Cmd+C.";
      }
    });
    const box = el("div", {
      class: "secret-once",
      role: "alert"
    }, el("h3", {
      text: title
    }), el("p", null, el("strong", {
      text: "Shown once. "
    }), "This secret will not be shown again and is not stored by this console. Put it in the bank's secret manager now."), id ? el("p", null, "ID: ", el("code", {
      text: id
    })) : null, value, note ? el("p", {
      class: "hint",
      text: note
    }) : null, el("div", {
      class: "row"
    }, copy, el("button", {
      type: "button",
      class: "btn btn-sm",
      text: "I have stored it — dismiss",
      onclick: () => {
        box.remove();
      }
    }), status));
    replace(slot, box);
    box.scrollIntoView({
      block: "nearest"
    });
    copy.focus();
  }
  async function loadIntegrations() {
    const kb = $("keys-body");
    const hb = $("hooks-body");
    const db = $("deliveries-body");
    renderHookEvents();
    const keysP = can("keys:manage") ? withBody(kb, async () => {
      const out = await api("GET", "/keys");
      renderKeyScopes(out.availableScopes || []);
      replace(kb, table([ {
        label: "Key ID",
        cls: "id",
        render: r => r.id
      }, {
        label: "Name",
        render: r => r.name
      }, {
        label: "Scopes",
        render: r => chips(r.scopes)
      }, {
        label: "Status",
        render: r => badge(r.status)
      }, {
        label: "Created",
        render: r => timeCell(r.createdAt)
      }, {
        label: "Last used",
        render: r => r.lastUsedAt ? timeCell(r.lastUsedAt) : "never"
      }, {
        label: "",
        render: r => r.status === "ACTIVE" ? el("button", {
          type: "button",
          class: "btn btn-sm btn-danger",
          text: "Revoke",
          "aria-label": `Revoke key ${r.name}`,
          onclick: () => revokeKey(r)
        }) : null
      } ], out.keys || [], {
        empty: "No API keys.",
        caption: "API keys"
      }));
    }) : null;
    const hooksP = withBody(hb, async () => {
      const out = await api("GET", "/webhooks");
      replace(hb, table([ {
        label: "ID",
        cls: "id",
        render: r => r.id
      }, {
        label: "URL",
        cls: "wrap-text",
        render: r => el("code", {
          text: r.url
        })
      }, {
        label: "Events",
        render: r => chips(r.events)
      }, {
        label: "Active",
        render: r => badge(r.active ? "ACTIVE" : "INACTIVE")
      } ], out.webhooks || [], {
        empty: "No webhooks.",
        caption: "Webhooks"
      }));
      replace(db, table([ {
        label: "When",
        render: r => timeCell(r.at)
      }, {
        label: "Event",
        render: r => el("code", {
          text: r.event
        })
      }, {
        label: "Status",
        render: r => badge(r.status)
      }, {
        label: "Attempts",
        num: true,
        render: r => String(r.attempts)
      }, {
        label: "Last HTTP",
        num: true,
        render: r => r.lastStatus === null || r.lastStatus === undefined ? null : String(r.lastStatus)
      }, {
        label: "Last error",
        cls: "wrap-text",
        render: r => r.lastError
      } ], out.deliveries || [], {
        empty: "No deliveries yet.",
        caption: "Recent webhook deliveries"
      }));
    });
    await Promise.all([ keysP, hooksP ]);
  }
  function renderKeyScopes(scopes) {
    const box = $("key-scopes");
    const key = scopes.join(",");
    if (state.keysScopes === key) return;
    state.keysScopes = key;
    replace(box, scopes.map(s => el("label", {
      class: "check"
    }, el("input", {
      type: "checkbox",
      value: s,
      checked: DEFAULT_SCOPES.includes(s)
    }), el("code", {
      text: s
    }))));
  }
  function renderHookEvents() {
    const box = $("hook-events");
    if (box.firstChild) return;
    replace(box, WEBHOOK_EVENTS.map(e => el("label", {
      class: "check"
    }, el("input", {
      type: "checkbox",
      value: e,
      checked: true
    }), el("code", {
      text: e
    }))));
  }
  async function revokeKey(k) {
    if (!window.confirm(`Revoke API key "${k.name}" (${k.id})? Requests signed with it will be rejected immediately. This cannot be undone.`)) return;
    try {
      await api("DELETE", `/keys/${encodeURIComponent(k.id)}`, undefined, {
        tenant: false
      });
      loadIntegrations();
    } catch (err) {
      if (err.status !== 401) window.alert(`${err.code}: ${err.message}`);
    }
  }
  async function onKeyCreate(ev) {
    ev.preventDefault();
    const err = $("key-error");
    showError(err, null);
    const scopes = [ ...$("key-scopes").querySelectorAll("input:checked") ].map(i => i.value);
    if (!scopes.length) {
      showError(err, {
        code: "SCOPES_REQUIRED",
        message: "select at least one scope"
      });
      return;
    }
    const btn = ev.submitter || ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const out = await api("POST", "/keys", {
        name: $("key-name").value.trim(),
        scopes: scopes
      });
      $("key-name").value = "";
      showSecretOnce("New API key secret", out.keyId, out.secret, out.note);
      loadIntegrations();
    } catch (e) {
      if (e.status !== 401) showError(err, e);
    } finally {
      btn.disabled = false;
    }
  }
  async function onHookCreate(ev) {
    ev.preventDefault();
    const err = $("hook-error");
    showError(err, null);
    const events = [ ...$("hook-events").querySelectorAll("input:checked") ].map(i => i.value);
    if (!events.length) {
      showError(err, {
        code: "EVENTS_REQUIRED",
        message: "select at least one event"
      });
      return;
    }
    const url = $("hook-url").value.trim();
    if (!/^https:\/\//i.test(url)) {
      showError(err, {
        code: "HTTPS_REQUIRED",
        message: "webhook endpoints should use https:// (the server makes the final check)"
      });
      if (!window.confirm("The URL is not https://. Submit anyway and let the server decide?")) return;
    }
    const btn = ev.submitter || ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const out = await api("POST", "/webhooks", {
        url: url,
        events: events
      });
      $("hook-url").value = "";
      showError(err, null);
      showSecretOnce("New webhook signing secret", out.id, out.secret, out.note);
      loadIntegrations();
    } catch (e) {
      if (e.status !== 401) showError(err, e);
    } finally {
      btn.disabled = false;
    }
  }
  async function loadAudit() {
    const body = $("audit-body");
    await withBody(body, async () => {
      const out = await api("GET", "/audit", undefined, {
        query: {
          limit: 200
        }
      });
      replace(body, table([ {
        label: "#",
        num: true,
        render: r => String(r.id)
      }, {
        label: "When",
        render: r => timeCell(r.at)
      }, {
        label: "Actor",
        cls: "id",
        render: r => r.actor
      }, {
        label: "Action",
        render: r => el("code", {
          text: r.action
        })
      }, {
        label: "Target",
        cls: "id",
        render: r => r.target
      }, {
        label: "Detail",
        cls: "wrap-text",
        render: r => r.detail && Object.keys(r.detail).length ? el("code", {
          text: JSON.stringify(r.detail)
        }) : null
      }, {
        label: "Hash",
        cls: "id",
        render: r => r.hash
      } ], out.entries || [], {
        empty: "No audit entries.",
        caption: "Audit log"
      }));
    });
  }
  async function onAuditVerify(ev) {
    const btn = ev.currentTarget;
    const box = $("audit-verify-result");
    btn.disabled = true;
    replace(box, el("p", {
      class: "muted",
      text: "Verifying the hash chain from genesis…"
    }));
    try {
      const out = await api("GET", "/audit/verify", undefined, {
        tenant: false
      });
      if (out.intact) {
        replace(box, el("div", {
          class: "result-banner s-ok"
        }, "Chain intact", el("span", {
          class: "sub"
        }, `${out.checked} entries verified from genesis (all tenants). Head: `, el("code", {
          text: String(out.head || "").slice(0, 24)
        }), "…")));
      } else {
        replace(box, el("div", {
          class: "result-banner s-bad"
        }, `Chain BROKEN at entry #${out.brokenAt}`, el("span", {
          class: "sub",
          text: `${out.checked} entries verified before the break. The log has been altered or truncated; escalate to the security team.`
        })));
      }
    } catch (err) {
      if (err.status !== 401) replace(box, errorBox(err));
    } finally {
      btn.disabled = false;
    }
  }
  async function onTenantCreate(ev) {
    ev.preventDefault();
    const box = $("tenant-result");
    const btn = ev.submitter || ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    clear(box);
    try {
      const out = await api("POST", "/tenants", {
        name: $("tenant-name").value.trim(),
        slug: $("tenant-slug").value.trim()
      }, {
        tenant: false
      });
      $("tenant-name").value = "";
      $("tenant-slug").value = "";
      replace(box, el("p", {
        class: "success"
      }, "Created ", el("strong", {
        text: out.name
      }), " (", el("code", {
        text: out.id
      }), ")."));
      state.me = await api("GET", "/me", undefined, {
        tenant: false
      });
      if (!state.tenant) store(TENANT_KEY, out.id);
      renderShell();
    } catch (err) {
      if (err.status !== 401) replace(box, errorBox(err));
    } finally {
      btn.disabled = false;
    }
  }
  async function onOperatorCreate(ev) {
    ev.preventDefault();
    const box = $("operator-result");
    clear(box);
    const pw = $("op-password").value;
    if (pw.length < 14) {
      replace(box, errorBox({
        code: "PASSWORD_SHORT",
        message: "the password must be at least 14 characters"
      }));
      return;
    }
    if (new Set(pw).size < 6) {
      replace(box, errorBox({
        code: "PASSWORD_WEAK",
        message: "choose a less repetitive password"
      }));
      return;
    }
    if (!state.tenant) {
      replace(box, errorBox({
        code: "TENANT_REQUIRED",
        message: "select a tenant first"
      }));
      return;
    }
    const btn = ev.submitter || ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const out = await api("POST", "/operators", {
        email: $("op-email").value.trim(),
        role: $("op-role").value,
        password: pw
      });
      $("op-email").value = "";
      $("op-password").value = "";
      replace(box, el("p", {
        class: "success"
      }, "Created ", el("strong", {
        text: out.email
      }), ` as ${out.role}. ${out.note || ""}`));
    } catch (err) {
      if (err.status !== 401) replace(box, errorBox(err));
    } finally {
      btn.disabled = false;
    }
  }
  function wire() {
    $("login-form").addEventListener("submit", onLogin);
    $("totp-form").addEventListener("submit", onTotp);
    $("totp-back").addEventListener("click", () => showSignin());
    $("totp-code").addEventListener("input", ev => {
      ev.target.value = ev.target.value.replace(/\D/g, "").slice(0, 6);
    });
    $("signout").addEventListener("click", onSignout);
    $("tenant-select").addEventListener("change", onTenantChange);
    $("nav").addEventListener("click", ev => {
      const b = ev.target.closest("button[data-view]");
      if (b) showView(b.dataset.view);
    });
    window.addEventListener("hashchange", () => {
      if (state.me) routeFromHash();
    });
    for (const b of document.querySelectorAll("[data-reload]")) {
      b.addEventListener("click", () => {
        if (b.dataset.reload === "policy" && state.dirty && !window.confirm("Reloading discards your unsaved policy changes. Continue?")) return;
        if (b.dataset.reload === "policy") {
          markDirty(false);
          loadPolicy(true);
          return;
        }
        reload(b.dataset.reload);
      });
    }
    $("txn-auto").addEventListener("change", armTxnTimer);
    $("txn-limit").addEventListener("change", () => reload("transactions"));
    $("decision-filter").addEventListener("submit", ev => {
      ev.preventDefault();
      state.decisionFilter = $("decision-txn").value.trim();
      reload("decisions");
    });
    $("decision-clear").addEventListener("click", () => {
      $("decision-txn").value = "";
      state.decisionFilter = "";
      reload("decisions");
    });
    $("review-status").addEventListener("change", () => reload("reviews"));
    $("customer-search").addEventListener("submit", onCustomerSearch);
    const pf = $("policy-form");
    pf.addEventListener("input", onPolicyFormInput);
    pf.addEventListener("change", onPolicyFormInput);
    $("pol-json").addEventListener("input", onPolicyJsonInput);
    pf.addEventListener("submit", onPolicySave);
    $("policy-defaults").addEventListener("click", onPolicyDefaults);
    $("policy-revert").addEventListener("click", onPolicyRevert);
    $("prompt-form").addEventListener("submit", onPromptPreview);
    $("key-form").addEventListener("submit", onKeyCreate);
    $("hook-form").addEventListener("submit", onHookCreate);
    $("audit-verify").addEventListener("click", onAuditVerify);
    $("tenant-form").addEventListener("submit", onTenantCreate);
    $("operator-form").addEventListener("submit", onOperatorCreate);
    window.addEventListener("beforeunload", ev => {
      if (state.dirty) {
        ev.preventDefault();
        ev.returnValue = "";
      }
    });
  }
  async function boot() {
    wire();
    if (!load(CSRF_KEY)) {
      showSignin();
      return;
    }
    try {
      await startApp();
    } catch (err) {
      if (err.status !== 401) showSignin(`${err.code}: ${err.message}`);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
