(function(global) {
  "use strict";
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function(k) {
      var v = attrs[k];
      if (v === undefined || v === null || v === false) return;
      if (k === "text") n.textContent = v; else if (k === "class") n.className = v; else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
    });
    (children || []).forEach(function(c) {
      if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function clear(n) {
    while (n.firstChild) n.removeChild(n.firstChild);
    return n;
  }
  function notice(kind, text) {
    return el("div", {
      class: "notice " + (kind || ""),
      role: kind === "bad" ? "alert" : "status",
      text: text
    });
  }
  function busy(button, on, label) {
    button.disabled = !!on;
    if (label) button.textContent = label;
  }
  var FRIENDLY = {
    OTP: "Authenticator code",
    FACE: "Face check",
    VOICE: "Voice check",
    UEBA: "Typing check"
  };
  function errorText(e) {
    var map = {
      TOKEN_INVALID: "This link has expired. Return to your bank and start again.",
      SESSION_EXPIRED: "This approval has expired. Return to your bank and start again.",
      RATE_LIMITED: "Too many attempts. Please wait a minute and try again.",
      CHALLENGE_INVALID: "That step timed out. We've prepared a fresh one.",
      CONSENT_REQUIRED: "Please accept the notice first.",
      CUSTOMER_UNAVAILABLE: "This account can't be used right now. Please contact your bank."
    };
    return e && map[e.code] || e && e.message || "Something went wrong. Please try again.";
  }
  var originsPromise = null;
  function postToParent(msg) {
    if (!global.parent || global.parent === global) return;
    var t = new URLSearchParams(global.location.search).get("t") || "";
    originsPromise = originsPromise || fetch("/c/v1/embed-origins?t=" + encodeURIComponent(t)).then(function(r) {
      return r.ok ? r.json() : {
        origins: []
      };
    }).then(function(d) {
      return d.origins || [];
    }, function() {
      return [];
    });
    originsPromise.then(function(origins) {
      origins.forEach(function(o) {
        global.parent.postMessage(msg, o);
      });
    });
  }
  var WINDOWS = /Windows/.test(navigator.userAgent);
  function mediaErrorText(err, kind) {
    var dev = kind === "microphone" ? "microphone" : "camera";
    var name = err && err.name || "";
    if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
      return "Your " + dev + " is blocked. Click the " + dev + " (or lock) icon in the address bar and choose Allow." + (WINDOWS ? " On Windows, also open Settings > Privacy & security > " + (dev === "camera" ? "Camera" : "Microphone") + " and turn on access, including “Let desktop apps access your " + dev + "”." : "") + " Then press Try again.";
    }
    if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
      return "Your " + dev + " couldn't start. It is probably in use by another app (Teams, Zoom, Skype or the " + (dev === "camera" ? "Camera" : "Voice Recorder") + " app) or another browser tab. Close it and press Try again.";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
      return "No " + dev + " was found. Connect one (or enable it in Device Manager) and press Try again.";
    }
    if (name === "BlackImage") {
      return "The camera picture is completely dark. Open the privacy shutter or press your laptop's camera key, " + "turn on a light, or choose another camera below.";
    }
    if (name === "NoPicture") return "The camera opened but isn't sending a picture. Choose another camera below, or press Try again.";
    if (name === "PolicyBlocked") {
      return "Your bank's page doesn't allow the " + dev + " here. (For the bank: the frame needs allow=\"camera; microphone\".)";
    }
    if (name === "InsecureContext") return "The " + dev + " needs a secure (https) connection.";
    if (name === "Unsupported") return "This browser can't use a " + dev + ". Use a current Chrome, Edge, Firefox or Safari.";
    return "We couldn't open your " + dev + (err && err.message ? " (" + err.message + ")" : "") + ". Press Try again.";
  }
  function setupCamera(cam, host, onReady, onProblem) {
    var box = el("div", {
      class: "media-status"
    });
    host.appendChild(box);
    var chosen = null;
    function picker(cams) {
      if (cams.length < 2) return null;
      var sel = el("select", {
        "aria-label": "Camera"
      });
      cams.forEach(function(c) {
        var o = el("option", {
          value: c.id,
          text: c.label
        });
        if (c.id === (chosen || cam.deviceId)) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", function() {
        chosen = sel.value;
        start();
      });
      return el("label", {
        class: "camera-pick"
      }, [ "Camera ", sel ]);
    }
    function start() {
      clear(box).appendChild(notice("", "Starting your camera… If your browser asks, choose Allow."));
      if (onProblem) onProblem(true);
      cam.start(chosen).then(function() {
        clear(box);
        return global.AMFA.listCameras().then(function(cams) {
          var p = picker(cams);
          if (p) box.appendChild(p);
        });
      }).then(function() {
        if (onReady) onReady();
      }, function(err) {
        cam.stop();
        clear(box).appendChild(notice("bad", mediaErrorText(err, "camera")));
        var again = el("button", {
          type: "button",
          class: "secondary",
          text: "Try again",
          onclick: start
        });
        box.appendChild(again);
        global.AMFA.listCameras().then(function(cams) {
          var p = picker(cams);
          if (p) box.appendChild(p);
        });
      });
    }
    start();
    return {
      retry: start
    };
  }
  global.UI = {
    el: el,
    clear: clear,
    notice: notice,
    busy: busy,
    FRIENDLY: FRIENDLY,
    errorText: errorText,
    postToParent: postToParent,
    mediaErrorText: mediaErrorText,
    setupCamera: setupCamera
  };
})(window);
