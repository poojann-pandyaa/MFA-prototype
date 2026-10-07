(function() {
  "use strict";
  var AMFA_ORIGIN = null, deviceId = null, current = null;
  function $(id) {
    return document.getElementById(id);
  }
  function api(method, path, body) {
    return fetch(path, {
      method: method,
      credentials: "same-origin",
      headers: body ? {
        "content-type": "application/json"
      } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function(r) {
      return r.json().then(function(d) {
        if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : String(r.status));
        return d;
      });
    });
  }
  function rupees(minor) {
    return "₹" + (minor / 100).toLocaleString("en-IN", {
      minimumFractionDigits: 2
    });
  }
  function frame(url, height) {
    var f = document.createElement("iframe");
    f.src = url;
    f.setAttribute("allow", "camera; microphone");
    f.setAttribute("referrerpolicy", "no-referrer");
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
    f.className = "embed";
    if (height) f.height = height;
    return f;
  }
  function discoverDevice(amfaBase) {
    AMFA_ORIGIN = new URL(amfaBase).origin;
    var f = frame(amfaBase + "/hosted/device?t=" + encodeURIComponent(window.BANK_TENANT_SLUG || ""), 0);
    f.className = "hidden";
    document.body.appendChild(f);
  }
  window.addEventListener("message", function(e) {
    if (e.origin !== AMFA_ORIGIN || !e.data || typeof e.data !== "object") return;
    if (e.data.type === "amfa:device") deviceId = e.data.deviceId;
    if (e.data.type === "amfa:enrolled") refresh();
    if (e.data.type === "amfa:status" && current && e.data.status !== "PENDING") finish();
  });
  function refresh() {
    return api("GET", "/api/me").then(function(me) {
      $("signin").classList.add("hidden");
      $("account").classList.remove("hidden");
      $("who").textContent = me.name + " (" + me.ref + ")";
      $("logout").classList.remove("hidden");
      $("balance").textContent = rupees(me.balance);
      var e = me.enrolled;
      $("enrolment").textContent = e ? "Secure approvals: " + Object.keys(e).filter(function(k) {
        return e[k] === true || e[k] === "ENROLLED";
      }).join(", ") : "Secure approvals are not set up yet.";
      $("transfer").classList.toggle("hidden", !e);
      var sel = $("payee");
      while (sel.firstChild) sel.removeChild(sel.firstChild);
      me.payees.forEach(function(p) {
        var o = document.createElement("option");
        o.value = p.ref;
        o.textContent = p.display;
        sel.appendChild(o);
      });
    });
  }
  var registering = false;
  function setMode(reg) {
    registering = reg;
    $("auth-title").textContent = reg ? "Open an account" : "Sign in to internet banking";
    $("name-row").classList.toggle("hidden", !reg);
    $("auth-hint").classList.toggle("hidden", !reg);
    $("auth-submit").textContent = reg ? "Open account" : "Sign in";
    $("switch-text").textContent = reg ? "Already have an account?" : "New to Demo Bank?";
    $("switch").textContent = reg ? "Sign in" : "Open an account";
    $("password").setAttribute("autocomplete", reg ? "new-password" : "current-password");
    $("auth-error").textContent = "";
  }
  $("switch").addEventListener("click", function() {
    setMode(!registering);
  });
  $("auth-form").addEventListener("submit", function(e) {
    e.preventDefault();
    var body = {
      username: $("username").value.trim().toLowerCase(),
      password: $("password").value
    };
    if (registering) {
      body.name = $("fullname").value.trim();
      if (!/^[a-z0-9][a-z0-9_-]{2,23}$/.test(body.username)) {
        $("auth-error").textContent = "Choose a username of 3–24 lowercase letters, digits, - or _.";
        return;
      }
      if (body.name.length < 2) {
        $("auth-error").textContent = "Enter your full name.";
        return;
      }
      if (body.password.length < 8) {
        $("auth-error").textContent = "Use a password of at least 8 characters.";
        return;
      }
    }
    $("auth-submit").disabled = true;
    api("POST", registering ? "/api/register" : "/api/login", body).then(function() {
      $("password").value = "";
      return refresh();
    }).catch(function(err) {
      $("auth-error").textContent = /^\d+$/.test(err.message) ? "Please check the details and try again." : err.message;
    }).then(function() {
      $("auth-submit").disabled = false;
    });
  });
  $("logout").addEventListener("click", function() {
    api("POST", "/api/logout").then(function() {
      location.reload();
    });
  });
  $("enrol").addEventListener("click", function() {
    api("POST", "/api/amfa/enrol").then(function(r) {
      var box = $("frame");
      box.classList.remove("hidden");
      while (box.firstChild) box.removeChild(box.firstChild);
      box.appendChild(frame(r.url, 720));
      $("transfer").classList.remove("hidden");
    });
  });
  $("form").addEventListener("submit", function(e) {
    e.preventDefault();
    $("outcome").textContent = "";
    var caps = {
      camera: true,
      microphone: true,
      pointer: matchMedia("(pointer: fine)").matches
    };
    api("POST", "/api/transfers", {
      payeeRef: $("payee").value,
      amountRupees: parseFloat($("amount").value),
      remarks: $("remarks").value,
      deviceId: deviceId || "unbound-device-0001",
      camera: caps.camera,
      microphone: caps.microphone,
      pointer: caps.pointer
    }).then(function(t) {
      current = t.transferId;
      $("plan").textContent = "Your bank needs: " + t.plan.required.join(" + ") + " (risk " + t.plan.riskBand + ": " + t.plan.reasoning + ")";
      var box = $("frame");
      while (box.firstChild) box.removeChild(box.firstChild);
      if (!t.challengeUrl) {
        $("outcome").textContent = "Referred for review by the bank.";
        return;
      }
      box.classList.remove("hidden");
      box.appendChild(frame(t.challengeUrl, 720));
    }).catch(function(err) {
      $("outcome").textContent = "Could not start: " + err.message;
    });
  });
  function finish() {
    var id = current;
    current = null;
    api("POST", "/api/transfers/" + id + "/complete").then(function(r) {
      $("frame").classList.add("hidden");
      $("outcome").textContent = r.status === "EXECUTED" ? "Sent. Reference " + r.reference + ". New balance " + rupees(r.balance) + "." : "Not sent: " + r.status;
      refresh();
    });
  }
  refresh().catch(function() {});
  fetch("/static/config.json").then(function(r) {
    return r.json();
  }).then(function(c) {
    window.BANK_TENANT_SLUG = c.tenantSlug;
    discoverDevice(c.amfaBase);
  });
})();
