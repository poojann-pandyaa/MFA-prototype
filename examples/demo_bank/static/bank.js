(function() {
  "use strict";
  var AMFA_ORIGIN = null, deviceId = null, me = null, current = null, pollTimer = null;
  var STATUS = {
    AWAITING_VERIFICATION: "Waiting for your checks", UNDER_REVIEW: "Under security review", DONE: "Completed",
    DECLINED: "Declined", EXPIRED: "Expired", CANCELLED: "Cancelled", FAILED: "Not completed"
  };
  var MONEY = { transfer: true, bill: true };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function api(method, path, body) {
    return fetch(path, {
      method: method, credentials: "same-origin",
      headers: body ? { "content-type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) {
          var err = new Error(typeof d.detail === "string" ? d.detail : "Something went wrong. Please try again.");
          err.status = r.status;
          throw err;
        }
        return d;
      });
    });
  }
  function rupees(minor) {
    return "₹" + (minor / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // ---------- device identity: AMFA's device page gives a stable id; demo can pretend to be a new phone ----------
  function storage(k, v) {
    try {
      if (v === undefined) return sessionStorage.getItem(k);
      if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v);
    } catch (e) { return null; }
  }
  function randomId(prefix) {
    var a = new Uint8Array(12);
    crypto.getRandomValues(a);
    return prefix + Array.prototype.map.call(a, function(b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  var deviceReady = new Promise(function(resolve) {
    window.addEventListener("message", function(e) {
      if (e.origin !== AMFA_ORIGIN || !e.data || typeof e.data !== "object") return;
      if (e.data.type === "amfa:device" && !deviceId) { deviceId = e.data.deviceId; resolve(); }
      if (e.data.type === "amfa:enrolled") { closeSheet(); refresh(); }
      if (e.data.type === "amfa:status" && current && e.data.status !== "PENDING") finish();
    });
    setTimeout(function() {   // AMFA device page unreachable: fall back to an id kept by this browser
      if (!deviceId) {
        try {
          deviceId = localStorage.getItem("bank.device") || randomId("web-");
          localStorage.setItem("bank.device", deviceId);
        } catch (e) { deviceId = randomId("web-"); }
      }
      resolve();
    }, 3000);
  });
  function currentDevice() { return storage("bank.demoDevice") || deviceId; }
  function caps() {
    return { camera: !!(navigator.mediaDevices), microphone: !!(navigator.mediaDevices),
             pointer: matchMedia("(pointer: fine)").matches };
  }
  function frame(url) {
    var f = document.createElement("iframe");
    f.src = url;
    f.setAttribute("allow", "camera; microphone");
    f.setAttribute("referrerpolicy", "no-referrer");
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
    f.className = "embed";
    return f;
  }

  // ---------- signed-out ----------
  var registering = false;
  function setMode(reg) {
    registering = reg;
    $("auth-title").textContent = reg ? "Open an account" : "Sign in";
    $("register-only").classList.toggle("hidden", !reg);
    $("auth-hint").classList.toggle("hidden", !reg);
    $("auth-submit").textContent = reg ? "Open account" : "Sign in";
    $("switch-text").textContent = reg ? "Already a customer?" : "New to Demo Bank?";
    $("switch").textContent = reg ? "Sign in" : "Open an account";
    $("password").setAttribute("autocomplete", reg ? "new-password" : "current-password");
    $("auth-error").textContent = "";
  }
  $("switch").addEventListener("click", function() { setMode(!registering); });
  $("auth-form").addEventListener("submit", function(e) {
    e.preventDefault();
    var body = { username: $("username").value.trim().toLowerCase(), password: $("password").value };
    if (registering) {
      body.name = $("fullname").value.trim();
      body.phone = $("phone").value.replace(/[\s-]/g, "");
      if (!/^[a-z0-9][a-z0-9_-]{2,23}$/.test(body.username)) return fail("Choose a username of 3–24 lowercase letters, digits, - or _.");
      if (body.name.length < 2) return fail("Enter your full name.");
      if (!/^\+?[0-9]{10,13}$/.test(body.phone)) return fail("Enter a 10-digit mobile number.");
      if (body.password.length < 8) return fail("Use a password of at least 8 characters.");
    }
    $("auth-submit").disabled = true;
    deviceReady.then(function() {
      body.deviceId = currentDevice();
      if (!registering) delete body.deviceId;
      return api("POST", registering ? "/api/register" : "/api/login", body);
    }).then(function() {
      $("password").value = "";
      return refresh();
    }).catch(function(err) { fail(err.message); }).then(function() { $("auth-submit").disabled = false; });
    function fail(msg) { $("auth-error").textContent = msg; }
  });
  $("logout").addEventListener("click", function() {
    api("POST", "/api/logout").then(function() { location.reload(); });
  });

  // ---------- signed-in shell ----------
  function show(tab) {
    ["home", "pay", "payees", "profile"].forEach(function(t) {
      $("tab-" + t).classList.toggle("hidden", t !== tab);
    });
    document.querySelectorAll("#tabs button").forEach(function(b) {
      b.classList.toggle("active", b.getAttribute("data-tab") === tab);
    });
    window.scrollTo(0, 0);
  }
  document.querySelectorAll("#tabs button").forEach(function(b) {
    b.addEventListener("click", function() { show(b.getAttribute("data-tab")); });
  });
  document.querySelectorAll(".seg").forEach(function(b) {
    b.addEventListener("click", function() {
      document.querySelectorAll(".seg").forEach(function(x) { x.classList.toggle("active", x === b); });
      $("transfer-form").classList.toggle("hidden", b.getAttribute("data-pay") !== "transfer");
      $("bill-form").classList.toggle("hidden", b.getAttribute("data-pay") !== "bill");
    });
  });

  function options(select, items) {
    var keep = select.value;
    clear(select);
    items.forEach(function(p) {
      var o = el("option", null, p.display);
      o.value = p.ref;
      select.appendChild(o);
    });
    if (keep) select.value = keep;
  }

  function refresh() {
    return deviceReady.then(function() {
      return api("GET", "/api/me?deviceId=" + encodeURIComponent(currentDevice()));
    }).then(function(m) {
      me = m;
      $("auth").classList.add("hidden");
      $("app").classList.remove("hidden");
      $("tabs").classList.remove("hidden");
      $("logout").classList.remove("hidden");
      $("who").textContent = m.name;
      $("acct-ref").textContent = m.ref;
      $("balance").textContent = rupees(m.balance);
      $("limit-bar").style.width = Math.min(100, 100 * m.sentToday / m.dailyLimit) + "%";
      $("limit-text").textContent = rupees(m.sentToday) + " of your " + rupees(m.dailyLimit) + " daily limit used today";
      $("cur-phone").textContent = m.phone || "not set";
      $("cur-email").textContent = m.email || "not set";
      var e = m.enrolled;
      var have = e ? Object.keys(e).filter(function(k) { return e[k] === true || e[k] === "ENROLLED"; }) : [];
      $("enrolment").textContent = have.length ? "Set up: " + have.join(", ").replace(/OTP_DELIVERY/, "SMS code").replace(/OTP_TOTP/, "authenticator app")
        : "Not set up yet. You need this before you can pay anyone.";
      options($("t-payee"), m.payees);
      options($("b-biller"), m.billers);
      options($("d-payee"), m.payees);
      renderPayees(m.payees);
      $("demo").classList.toggle("hidden", !m.demoTools);
      banner();
      return loadActivity();
    }).catch(function(err) {
      if (err.status !== 401) throw err;
    });
  }

  function banner() {
    var b = $("banner"), act = $("banner-action");
    if (!me.enrolled) {
      $("banner-text").textContent = "Set up secure approvals to start banking. It takes about two minutes.";
      act.textContent = "Set up";
      act.onclick = enrol;
    } else if (!me.deviceTrusted) {
      $("banner-text").textContent = "This device isn't verified for your account yet.";
      act.textContent = "Verify device";
      act.onclick = function() { start("device", {}, "Verify this device"); };
    } else {
      b.classList.add("hidden");
      return;
    }
    b.classList.remove("hidden");
  }

  function renderPayees(list) {
    var ul = $("payees");
    clear(ul);
    list.forEach(function(p) {
      var li = el("li");
      var row = el("div", "row");
      row.appendChild(el("span", "title", p.display));
      var pay = el("button", "link", "Pay");
      pay.type = "button";
      pay.addEventListener("click", function() { $("t-payee").value = p.ref; show("pay"); $("t-amount").focus(); });
      row.appendChild(pay);
      li.appendChild(row);
      ul.appendChild(li);
    });
  }

  // ---------- activity ----------
  function loadActivity() {
    return api("GET", "/api/activity").then(function(d) {
      $("balance").textContent = rupees(d.balance);
      var ul = $("activity");
      clear(ul);
      $("activity-empty").classList.toggle("hidden", d.activity.length > 0);
      var waiting = false;
      d.activity.forEach(function(a) {
        if (a.status === "UNDER_REVIEW" || a.status === "AWAITING_VERIFICATION") waiting = true;
        ul.appendChild(item(a));
      });
      clearTimeout(pollTimer);
      if (waiting) pollTimer = setTimeout(loadActivity, 5000);   // e.g. a review an operator is deciding
    });
  }

  function item(a) {
    var li = el("li");
    var row = el("div", "row");
    row.appendChild(el("span", "title", a.title));
    if (MONEY[a.kind]) row.appendChild(el("span", "amount", "−" + rupees(a.amountMinor)));
    li.appendChild(row);
    var meta = el("div", "row");
    meta.appendChild(el("span", "muted small", new Date(a.createdAt * 1000).toLocaleString("en-IN",
      { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })));
    meta.appendChild(el("span", "chip " + a.status, STATUS[a.status] || a.status));
    li.appendChild(meta);
    if (a.detail) li.appendChild(el("p", "muted small", a.detail));
    var actions = el("div", "actions");
    if (a.status === "UNDER_REVIEW" || a.status === "AWAITING_VERIFICATION") {
      actions.appendChild(button("Cancel", function() {
        api("POST", "/api/actions/" + a.id + "/cancel").then(refresh);
      }));
    }
    if (a.status === "DONE" && !a.reported) {
      actions.appendChild(button("This wasn't me", function() {
        if (!confirm("Report this as something you didn't do? Our fraud team will contact you.")) return;
        api("POST", "/api/actions/" + a.id + "/report", { reason: "" }).then(loadActivity);
      }));
    }
    if (a.reported) actions.appendChild(el("span", "muted small", "Reported to the fraud team"));
    if (actions.firstChild) li.appendChild(actions);
    return li;
  }
  function button(text, onClick) {
    var b = el("button", "link", text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  // ---------- protected actions: every one goes through the verification sheet ----------
  function start(kind, body, what, form) {
    var err = form && form.querySelector(".error");
    if (err) err.textContent = "";
    var submit = form && form.querySelector("button[type=submit]");
    if (submit) submit.disabled = true;
    return deviceReady.then(function() {
      body.deviceId = currentDevice();
      var c = caps();
      body.camera = c.camera; body.microphone = c.microphone; body.pointer = c.pointer;
      return api("POST", "/api/actions/" + kind, body);
    }).then(function(r) {
      if (form) form.reset();
      openSheet(r.action, r.challengeUrl, what);
      loadActivity();
    }).catch(function(e) {
      if (err) err.textContent = e.message; else alert(e.message);
    }).then(function() { if (submit) submit.disabled = false; });
  }

  function openSheet(action, url, what) {
    current = action.id;
    $("sheet-title").textContent = "Confirm it's you";
    $("sheet-what").textContent = what;
    $("sheet-reason").textContent = action.reason || "";
    $("sheet-checks").textContent = url && action.checks && action.checks.length ? "You'll be asked for: " + action.checks.join(", ") : "";
    $("scam").classList.toggle("hidden", !action.scamWarning);
    $("sheet-outcome").textContent = "";
    $("sheet-outcome").className = "outcome";
    clear($("sheet-frame"));
    if (url) {
      $("sheet-frame").appendChild(frame(url));
      $("sheet-cancel").classList.add("hidden");
    } else {
      outcome(action);
    }
    $("sheet").classList.remove("hidden");
  }

  function outcome(a) {
    var o = $("sheet-outcome");
    var text = {
      DONE: "Done. " + (MONEY[a.kind] ? "The money is on its way." : "Your change is saved."),
      UNDER_REVIEW: "We've paused this for a quick security review. You'll see the result in your activity, usually within a few minutes. You can cancel it while you wait.",
      DECLINED: "We couldn't approve this. If you need help, call the number on the back of your card.",
      EXPIRED: "This request timed out. Please start again.",
      CANCELLED: "Cancelled. Nothing was changed.",
      FAILED: "This didn't go through" + (a.detail ? ": " + a.detail : ".")
    }[a.status] || STATUS[a.status] || a.status;
    o.textContent = text;
    o.className = "outcome " + (a.status === "DONE" ? "ok" : a.status === "UNDER_REVIEW" ? "" : "bad");
    $("sheet-cancel").classList.toggle("hidden", a.status !== "UNDER_REVIEW");
  }

  function finish() {
    var id = current;
    api("POST", "/api/actions/" + id + "/complete").then(function(r) {
      clear($("sheet-frame"));
      outcome(r.action);
      refresh();
    });
  }

  function closeSheet() {
    $("sheet").classList.add("hidden");
    clear($("sheet-frame"));
    current = null;
  }
  $("sheet-close").addEventListener("click", function() { closeSheet(); loadActivity(); });
  $("sheet-cancel").addEventListener("click", function() {
    api("POST", "/api/actions/" + current + "/cancel").then(function(r) { outcome(r.action); loadActivity(); });
  });

  function enrol() {
    api("POST", "/api/amfa/enrol").then(function(r) {
      current = null;
      $("sheet-title").textContent = "Set up secure approvals";
      $("sheet-what").textContent = "";
      $("sheet-reason").textContent = "We'll use your face, voice, typing and a one-time code to confirm payments are really yours.";
      $("sheet-checks").textContent = "";
      $("scam").classList.add("hidden");
      $("sheet-outcome").textContent = "";
      $("sheet-cancel").classList.add("hidden");
      clear($("sheet-frame"));
      $("sheet-frame").appendChild(frame(r.url));
      $("sheet").classList.remove("hidden");
    });
  }
  $("enrol").addEventListener("click", enrol);

  function amount(id) { return parseFloat($(id).value); }
  function chosenText(id) { var s = $(id); return s.options[s.selectedIndex] ? s.options[s.selectedIndex].text : ""; }

  $("transfer-form").addEventListener("submit", function(e) {
    e.preventDefault();
    start("transfer", { payeeRef: $("t-payee").value, amountRupees: amount("t-amount") },
          "Send " + rupees(Math.round(amount("t-amount") * 100)) + " to " + chosenText("t-payee"), this);
  });
  $("bill-form").addEventListener("submit", function(e) {
    e.preventDefault();
    start("bill", { billerRef: $("b-biller").value, amountRupees: amount("b-amount") },
          "Pay " + rupees(Math.round(amount("b-amount") * 100)) + " to " + chosenText("b-biller"), this);
  });
  $("payee-form").addEventListener("submit", function(e) {
    e.preventDefault();
    var acct = $("p-account").value.trim();
    start("payee", { name: $("p-name").value.trim(), account: acct, ifsc: $("p-ifsc").value.trim().toUpperCase() },
          "Add " + $("p-name").value.trim() + " (account ending " + acct.slice(-4) + ")", this);
  });
  $("limit-form").addEventListener("submit", function(e) {
    e.preventDefault();
    start("limit", { limitRupees: amount("l-amount") },
          "Set your daily limit to " + rupees(Math.round(amount("l-amount") * 100)), this);
  });
  $("contact-form").addEventListener("submit", function(e) {
    e.preventDefault();
    var field = $("c-field").value;
    start("contact", { field: field, value: $("c-value").value.trim() },
          "Change your " + (field === "phone" ? "mobile number" : "email"), this);
  });
  $("password-form").addEventListener("submit", function(e) {
    e.preventDefault();
    start("password", { current: $("pw-current").value, new: $("pw-new").value }, "Change your password", this);
  });

  // ---------- demo tools ----------
  function demoSays(msg) { $("d-status").textContent = msg; }
  $("d-sim").addEventListener("click", function() {
    api("POST", "/api/demo/sim-swap").then(function() { demoSays("SIM swap recorded. Try a payment now."); });
  });
  $("d-flag").addEventListener("click", function() {
    api("POST", "/api/demo/payee-risk", { payeeRef: $("d-payee").value, flagged: true }).then(function() {
      demoSays(chosenText("d-payee") + " is now on the risk list. Try paying them.");
    });
  });
  $("d-device").addEventListener("click", function() {
    storage("bank.demoDevice", randomId("demo-phone-"));
    demoSays("This browser now looks like a new phone.");
    refresh().then(function() { show("home"); });
  });
  $("d-reset").addEventListener("click", function() {
    storage("bank.demoDevice", null);
    api("POST", "/api/demo/reset").then(function() { demoSays("Demo signals cleared."); refresh(); });
  });

  // ---------- boot ----------
  fetch("/static/config.json").then(function(r) { return r.json(); }).then(function(c) {
    AMFA_ORIGIN = new URL(c.amfaBase).origin;
    var f = frame(c.amfaBase + "/hosted/device?t=" + encodeURIComponent(c.tenantSlug || ""));
    f.className = "hidden";
    f.title = "device";
    document.body.appendChild(f);
  });
  refresh();
})();
