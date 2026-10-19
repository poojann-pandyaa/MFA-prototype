(function() {
  "use strict";
  var el = UI.el, clear = UI.clear;
  var token = AMFA.takeTokenFromFragment();
  var api = token ? new AMFA.Client("", token) : null;
  var panel = document.getElementById("panel");
  var camera = null, mic = null, keys = null, tracer = null, talk = null, current = null;
  function notifyParent(status) {
    UI.postToParent({
      type: "amfa:status",
      status: status
    });
  }
  function teardown() {
    if (camera) {
      camera.stop();
      camera = null;
    }
    if (mic) {
      try {
        mic.stop();
      } catch (e) {}
      mic = null;
    }
    if (keys) {
      keys.detach();
      keys = null;
    }
    if (tracer) {
      tracer.detach();
      tracer = null;
    }
    if (talk) {
      talk.stop();
      talk = null;
    }
  }
  function isMoney(a) {   // account changes are sent with a zero amount
    return /[1-9]/.test(a.amount || "");
  }
  // every check opens with what is being approved, so it can't be mistaken for setting up
  function banner(st) {
    var a = st.approving, done = st.steps.filter(function(s) { return s.status === "PASSED"; }).length;
    var what = isMoney(a) ? a.amount + " to " + a.payee : a.type.replace(/_/g, " ").toLowerCase();
    return UI.modeBanner("verify", "Approving " + what + " · check " + Math.min(done + 1, st.steps.length) + " of " + st.steps.length);
  }
  function renderHeader(st) {
    UI.applyTheme(st.theme);
    var passed = st.steps.filter(function(s) {
      return s.status === "PASSED";
    }).length;
    if (st.challenge) UI.step("session", passed, st.steps.length, st.challenge.factor);
    document.getElementById("bank").textContent = st.approving.bank;
    document.getElementById("kind").textContent = st.approving.type.replace(/_/g, " ");
    document.getElementById("approving-title").textContent = st.approving.amount;
    document.getElementById("payee").textContent = st.approving.payee;
    var steps = clear(document.getElementById("steps"));
    var current = st.challenge ? st.challenge.factor : null, marked = false;
    st.steps.forEach(function(s) {
      var cls = s.status === "PASSED" ? "done" : /FAILED|SPOOF/.test(s.status) ? "failed" : "";
      if (!marked && s.status === "PENDING" && s.factor === current) {
        cls = "current";
        marked = true;
      }
      steps.appendChild(el("li", {
        class: cls,
        text: (s.status === "PASSED" ? "✓ " : "") + UI.FRIENDLY[s.factor]
      }));
    });
    document.getElementById("approving").classList.remove("hidden");
  }
  // After an approval that passed a face or voice check, this device keeps its own key, so later
  // approvals from it prove it is this device and not a copied ID. Silent: a refusal changes nothing.
  function keepDeviceKey(st) {
    var live = st.steps.some(function(s) {
      return (s.factor === "FACE" || s.factor === "VOICE") && s.status === "PASSED";
    });
    if (st.status !== "VERIFIED" || st.deviceBound || !live) return Promise.resolve();
    var add = AMFA.deviceKey().then(function(k) {
      return api.post("/c/v1/session/device-key", {
        deviceId: k.deviceId,
        publicJwk: k.publicJwk
      });
    }).catch(function() {});
    // the bank closes this page when told the outcome: let the key arrive first, but never wait long
    return Promise.race([ add, new Promise(function(r) { setTimeout(r, 4000); }) ]);
  }
  function final(st) {
    teardown();
    keepDeviceKey(st).then(function() {
      notifyParent(st.status);
    });
    var a = st.approving;
    var money = isMoney(a);
    var map = {
      VERIFIED: [ "ok", "✓", "Verified: transaction approved", "Your bank will now complete it and show you the final result." ],
      BLOCKED: [ "bad", "✕", "Not approved", "We could not verify it was you, so nothing was sent. Your bank has been notified. If you did not start this, contact your bank now." ],
      MANUAL_REVIEW: [ "warn", "…", "On hold for a quick review", "Nothing has been sent yet. Your bank will review it and let you know." ],
      EXPIRED: [ "warn", "⧗", "Timed out: not approved", "Nothing was sent. Return to your bank and start again." ]
    }[st.status] || [ "warn", "?", st.status, "" ];
    var passed = st.steps.filter(function(s) { return s.status === "PASSED"; }).map(function(s) { return UI.FRIENDLY[s.factor]; });
    var rows = [];
    function row(k, v) { if (v) rows.push(el("div", { class: "kv" }, [ el("span", { text: k }), el("span", { text: v }) ])); }
    row(money ? "Amount" : "Request", money ? a.amount : a.type.replace(/_/g, " ").toLowerCase());
    row("To", money ? a.payee : "");
    row("Verified with", passed.join(", ") || "none");
    clear(panel).appendChild(el("div", {
      class: "result " + map[0]
    }, [ el("div", {
      class: "icon",
      "aria-hidden": "true",
      text: map[1]
    }), el("h2", {
      text: map[2]
    }), el("div", {
      class: "outcome"
    }, rows), el("p", {
      class: "muted",
      text: map[3]
    }) ]));
  }
  function load(message) {
    return api.get("/c/v1/session").then(function(st) {
      show(st, message);
    }).catch(function(e) {
      fatal(e);
    });
  }
  function fatal(e) {
    teardown();
    clear(panel).appendChild(UI.notice("bad", UI.errorText(e)));
    notifyParent("ERROR");
  }
  function submit(ch, payload, button) {
    UI.busy(button, true, "Checking…");
    return api.post("/c/v1/session/factors/" + ch.factor, {
      challengeId: ch.challengeId,
      payload: payload
    }).then(function(out) {
      teardown();
      var r = out.result, msg = null;
      if (r.retry) msg = UI.notice("bad", r.guidance || "That didn't work. Let's try once more."); else if (out.escalation && out.escalation.appended && out.escalation.appended.length) {
        msg = UI.notice("warn", "One more check is needed to keep your account safe.");
      }
      show(out.state, msg);
    }).catch(function(e) {
      teardown();
      if (e.code === "CHALLENGE_INVALID") return load(UI.notice("warn", UI.errorText(e)));
      fatal(e);
    });
  }
  function show(st, message) {
    renderHeader(st);
    if (st.status !== "PENDING") return final(st);
    var ch = st.challenge;
    clear(panel);
    panel.appendChild(banner(st));
    if (message) panel.appendChild(message);
    if (!ch) return panel.appendChild(UI.notice("", "Waiting…"));
    ({
      OTP: otpStep,
      FACE: faceStep,
      VOICE: voiceStep,
      UEBA: typingStep
    })[ch.factor](ch);
  }
  function otpStep(ch) {
    var totp = ch.params.mode === "totp";
    var btn = el("button", {
      type: "submit",
      text: "Verify"
    });
    var sent = false;
    var boxes = UI.codeBoxes(function(code) {
      if (!sent) {
        sent = true;
        submit(ch, {
          code: code
        }, btn);
      }
    });
    var timer = el("p", {
      class: "muted small"
    });
    var form = el("form", {}, [ el("h2", {
      text: totp ? "Verify with your authenticator code" : "Verify with the code we sent"
    }), el("p", {
      class: "muted",
      id: "otp-help",
      text: totp ? "Open your authenticator app and enter the 6-digit code for this bank." : "We sent it to " + ch.params.sentTo + ". The message names this amount and payee, so check they match."
    }), boxes.node, timer, el("p", {
      class: "notice warn",
      text: "Never share this code. Anyone asking for it, even someone saying they're from the bank, is trying to take your money."
    }), btn ]);
    boxes.input.setAttribute("aria-describedby", "otp-help");
    if (!totp) {
      // a new code can be asked for after a short wait (the server enforces it too), or at once when this one expires
      var resend = el("button", {
        type: "button",
        class: "secondary",
        disabled: "disabled"
      });
      var canResendAt = Date.now() / 1000 + 30, problem = null;
      var waitTick = function() {
        var s = Math.ceil(canResendAt - Date.now() / 1000);
        resend.textContent = s > 0 ? "Resend code in " + s + "s" : "Resend code";
        resend.disabled = s > 0;
        if (s > 0) setTimeout(waitTick, 1000);
      };
      resend.addEventListener("click", function() {
        UI.busy(resend, true, "Sending…");
        api.post("/c/v1/session/otp/resend", {}).then(function(st) {
          show(st, UI.notice("ok", "We've sent a new code to " + ch.params.sentTo + ". Earlier codes no longer work."));
        }).catch(function(e) {
          if (problem) problem.remove();
          problem = form.insertBefore(UI.notice("bad", UI.errorText(e)), btn);
          if (e.code === "OTP_SEND_LIMIT") return UI.busy(resend, true, "No more codes for this request");
          canResendAt = Date.now() / 1000 + 30;
          waitTick();
        });
      });
      form.appendChild(resend);
      waitTick();
      UI.countdown(timer, ch.expiresAt, "Enter it within ", function() {
        canResendAt = 0;
        waitTick();
      });
    }
    form.addEventListener("submit", function(e) {
      e.preventDefault();
      var code = boxes.value();
      if (code.length !== 6 || sent) {
        boxes.input.focus();
        return;
      }
      sent = true;
      submit(ch, {
        code: code
      }, btn);
    });
    panel.appendChild(form);
    boxes.input.focus();
  }
  function faceStep(ch) {
    var dir = ch.params.action === "TURN_LEFT" ? "LEFT" : "RIGHT";
    var face = UI.faceCamera();
    var video = face.video;
    var prompt = face.prompt;
    prompt.textContent = "Look straight at the camera";
    var btn = el("button", {
      type: "button",
      text: "Start face check"
    });
    panel.appendChild(el("h2", {
      text: "Verify with your face"
    }));
    panel.appendChild(el("p", {
      class: "muted",
      text: "Keep your face in the circle. When asked, slowly turn your head to your " + dir.toLowerCase() + " and hold it until the ring is full."
    }));
    panel.appendChild(face.node);
    camera = new AMFA.Camera(video);
    btn.disabled = true;
    UI.setupCamera(camera, panel, function() {
      btn.disabled = false;
    }, function() {
      btn.disabled = true;
    });
    panel.appendChild(btn);
    btn.addEventListener("click", function() {
      UI.busy(btn, true, "Hold still…");
      var frames = [], interval = ch.params.intervalMs || 150, total = ch.params.frames || 12;
      AMFA.sleep(600).then(function() {
        return camera.burst(3, interval, function(i) {
          face.setProgress((i + 1) / total);
        });
      }).then(function(f) {
        frames = frames.concat(f);
        prompt.textContent = "Now slowly turn your head to your " + dir.toLowerCase();
        face.setArrow(dir);
        var n = Math.max(3, total - 3);
        return camera.burst(n, interval, function(i) {
          face.setProgress((i + 4) / total);
          if (i === n - 6) prompt.textContent = "Hold it there…";   // the last frames must show the turned pose
        });
      }).then(function(f) {
        face.setArrow(null);
        prompt.textContent = "Done, checking…";
        submit(ch, {
          frames: frames.concat(f)
        }, btn);
      }, function(err) {   // the camera stalled: nothing was sent, so let the customer try again
        face.setArrow(null);
        face.setProgress(0);
        prompt.textContent = "Look straight at the camera";
        UI.busy(btn, false, "Try again");
        panel.insertBefore(UI.notice("bad", UI.mediaErrorText(err, "camera")), btn);
      });
    });
  }
  function voiceStep(ch) {
    var minS = ch.params.minSeconds || 3, maxS = ch.params.maxSeconds || 10;
    panel.appendChild(el("h2", {
      text: "Verify with your voice"
    }));
    panel.appendChild(el("p", {
      class: "muted",
      text: "Hold the button and read these words aloud:"
    }));
    panel.appendChild(el("div", {
      class: "prompt-phrase",
      text: ch.params.prompt
    }));
    talk = UI.holdToTalk({
      minSeconds: minS,
      maxSeconds: maxS,
      idleText: "Hold the button, read the words, then let go.",
      onRecorded: function(out) {
        talk.button.disabled = true;
        var sending = el("button", { type: "button", class: "hidden" });
        panel.appendChild(sending);
        submit(ch, {
          audio: out.wav
        }, sending);
      },
      onError: function(err) {
        panel.appendChild(UI.notice("bad", UI.mediaErrorText(err, "microphone")));
      }
    });
    panel.appendChild(talk.node);
  }
  function typingStep(ch) {
    var phrase = ch.params.phrase;
    var input = el("input", {
      type: "text",
      class: "phrase",
      id: "phrase",
      "aria-describedby": "phrase-help"
    });
    var btn = el("button", {
      type: "submit",
      text: "Continue"
    });
    var canvasWrap = el("div");
    var form = el("form", {}, [ el("h2", {
      text: "Verify with your typing"
    }), el("p", {
      class: "muted",
      id: "phrase-help",
      text: "Type this phrase exactly, at your normal pace:"
    }), el("div", {
      class: "prompt-phrase",
      text: phrase
    }), el("label", {
      for: "phrase",
      text: "Phrase"
    }), input, canvasWrap, btn ]);
    panel.appendChild(form);
    keys = new AMFA.KeystrokeRecorder(input);
    var trace = null;
    if (ch.params.shape) {
      var canvas = el("canvas", {
        "aria-label": "Trace the line from the green dot to the red dot"
      });
      canvasWrap.appendChild(el("p", {
        class: "muted",
        text: "Then trace the line from the green dot to the red dot."
      }));
      canvasWrap.appendChild(canvas);
      var traceKind = null, note = null;
      requestAnimationFrame(function() {
        tracer = new AMFA.ShapeTracer(canvas, ch.params.shape, function(pts, pointerType) {
          trace = pts;
          traceKind = pointerType;
        }, {
          input: ch.params.input || "keyboard",
          onRefused: function(msg) {
            if (note) note.remove();
            note = UI.notice("warn", msg);
            canvasWrap.insertBefore(note, canvas);
          }
        });
      });
    }
    input.focus();
    form.addEventListener("submit", function(e) {
      e.preventDefault();
      if (input.value !== phrase) {
        panel.insertBefore(UI.notice("bad", "The phrase doesn't match. Clear it and type it again."), form);
        input.value = "";
        keys.take();
        input.focus();
        return;
      }
      if (ch.params.shape && !trace) {
        panel.insertBefore(UI.notice("warn", "Please trace the line as well."), form);
        return;
      }
      // how each part was entered; the service checks it against the challenge's template set
      var payload = {
        keystrokeSample: keys.take(),
        keystrokeInput: ch.params.input || "keyboard"
      };
      if (trace) {
        payload.shapeTrace = trace;
        payload.shapePointerType = traceKind;
      }
      submit(ch, payload, btn);
    });
  }
  function bindDevice(st) {
    if (st.deviceBound || st.status !== "PENDING") return Promise.resolve(st);
    return AMFA.hasDeviceKey().then(function(has) {
      if (!has) return st;
      return AMFA.signWithDevice("amfa-device|" + st.sessionId + "|" + st.deviceNonce).then(function(sig) {
        return api.post("/c/v1/session/device", {
          deviceId: AMFA.deviceId(),
          signature: sig
        });
      }).then(function() {
        return st;
      }, function() {
        return st;
      });
    });
  }
  if (!api) {
    clear(panel).appendChild(UI.notice("bad", "This link is incomplete. Return to your bank and start again."));
    return;
  }
  api.get("/c/v1/session").then(bindDevice).then(function() {
    return load();
  }).catch(fatal);
})();
