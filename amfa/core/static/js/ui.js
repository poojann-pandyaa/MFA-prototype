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
    if (name === "CameraStalled") return "Your camera stopped sending new pictures. Move to brighter light, keep this page open, and press Try again.";
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
  // ---------- the bank's look, and running inside the bank's app ----------
  var embedded = new URLSearchParams(global.location.search).get("embed") === "1";
  if (embedded) document.documentElement.classList.add("embedded");
  function rgb(hex) {
    return [ 1, 3, 5 ].map(function(i) {
      return parseInt(hex.slice(i, i + 2), 16);
    });
  }
  function luminance(c) {
    var l = c.map(function(v) {
      v /= 255;
      return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4);
    });
    return .2126 * l[0] + .7152 * l[1] + .0722 * l[2];
  }
  function applyTheme(theme) {
    if (!theme || !/^#[0-9A-Fa-f]{6}$/.test(theme.color || "")) return;
    var c = rgb(theme.color);
    if (global.matchMedia && global.matchMedia("(prefers-color-scheme: dark)").matches) {
      c = c.map(function(v) {
        return Math.round(v + (255 - v) * .45);   // lift it so it reads on a dark page
      });
    }
    var root = document.documentElement.style;
    root.setProperty("--brand", "rgb(" + c.join(",") + ")");
    root.setProperty("--focus", "rgb(" + c.join(",") + ")");
    var L = luminance(c), dark = luminance([ 15, 27, 45 ]);   // text on the brand colour: whichever contrasts more
    root.setProperty("--brand-ink", 1.05 / (L + .05) >= (L + .05) / (dark + .05) ? "#ffffff" : "#0f1b2d");
    root.setProperty("--brand-soft", "rgba(" + c.join(",") + ",0.12)");
  }
  function step(flow, index, total, factor) {
    postToParent({
      type: "amfa:step",
      flow: flow,
      index: index,
      total: total,
      factor: factor
    });
  }
  // ---------- the face camera: a circle with a progress ring and a turn arrow ----------
  var SVG = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    var n = document.createElementNS(SVG, tag);
    Object.keys(attrs).forEach(function(k) {
      n.setAttribute(k, attrs[k]);
    });
    return n;
  }
  function faceCamera() {
    var C = 2 * Math.PI * 47;
    var video = el("video", {
      autoplay: true,
      muted: true,
      playsinline: true,
      "aria-label": "Camera preview"
    });
    var ring = svgEl("svg", {
      viewBox: "0 0 100 100",
      class: "face-ring",
      "aria-hidden": "true"
    });
    ring.appendChild(svgEl("circle", {
      cx: 50,
      cy: 50,
      r: 47,
      class: "track"
    }));
    var bar = svgEl("circle", {
      cx: 50,
      cy: 50,
      r: 47,
      class: "bar",
      "stroke-dasharray": C.toFixed(1),
      "stroke-dashoffset": C.toFixed(1)
    });
    ring.appendChild(bar);
    var arrow = el("div", {
      class: "face-arrow hidden",
      "aria-hidden": "true"
    });
    var prompt = el("p", {
      class: "face-prompt",
      role: "status"
    });
    var node = el("div", {
      class: "face"
    }, [ prompt, el("div", {
      class: "face-circle"
    }, [ el("div", {
      class: "camera"
    }, [ video ]), ring, arrow ]) ]);
    return {
      node: node,
      video: video,
      prompt: prompt,
      setProgress: function(f) {
        bar.setAttribute("stroke-dashoffset", (C * (1 - Math.max(0, Math.min(1, f)))).toFixed(1));
      },
      setArrow: function(dir) {   // "LEFT", "RIGHT" (the customer's own side) or null
        arrow.className = "face-arrow" + (dir ? " " + dir.toLowerCase() : " hidden");
        arrow.textContent = dir === "LEFT" ? "←" : dir === "RIGHT" ? "→" : "";
      }
    };
  }
  // ---------- six boxes for a one-time code, over one real input so autofill and paste work ----------
  function codeBoxes(onComplete) {
    var input = el("input", {
      type: "tel",
      class: "code-input",
      id: "code",
      inputmode: "numeric",
      maxlength: "6",
      autocomplete: "one-time-code",
      "aria-label": "6-digit code"
    });
    var boxes = [ 0, 1, 2, 3, 4, 5 ].map(function() {
      return el("span", {
        class: "code-box",
        "aria-hidden": "true"
      });
    });
    function paint() {
      var v = input.value.replace(/\D/g, "").slice(0, 6);
      if (v !== input.value) input.value = v;
      boxes.forEach(function(b, i) {
        b.textContent = v[i] || "";
        b.className = "code-box" + (v[i] ? " filled" : "") + (document.activeElement === input && i === Math.min(v.length, 5) ? " current" : "");
      });
      if (v.length === 6 && onComplete) onComplete(v);
    }
    [ "input", "focus", "blur" ].forEach(function(e) {
      input.addEventListener(e, paint);
    });
    var node = el("div", {
      class: "code-boxes"
    }, boxes.concat([ input ]));
    node.addEventListener("click", function() {
      input.focus();
    });
    paint();
    return {
      node: node,
      input: input,
      value: function() {
        return input.value.replace(/\D/g, "");
      },
      clear: function() {
        input.value = "";
        paint();
      }
    };
  }
  function countdown(node, expiresAt, prefix) {
    function tick() {
      var s = Math.max(0, Math.round(expiresAt - Date.now() / 1000));
      node.textContent = s ? prefix + Math.floor(s / 60) + ":" + ("0" + s % 60).slice(-2) : "This code has expired. Ask for a new one.";
      if (s && node.isConnected) setTimeout(tick, 1000);
    }
    tick();
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
    setupCamera: setupCamera,
    embedded: embedded,
    applyTheme: applyTheme,
    step: step,
    faceCamera: faceCamera,
    codeBoxes: codeBoxes,
    countdown: countdown
  };
})(window);
