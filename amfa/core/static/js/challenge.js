(function() {
  "use strict";
  var el = UI.el, clear = UI.clear;
  var token = AMFA.takeTokenFromFragment();
  var api = token ? new AMFA.Client("", token) : null;
  var panel = document.getElementById("panel");
  var camera = null, mic = null, keys = null, tracer = null;
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
  }
  function renderHeader(st) {
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
  function final(st) {
    teardown();
    notifyParent(st.status);
    var map = {
      VERIFIED: [ "ok", "✓", "Approved", "You can return to your bank app. The transaction will now be processed." ],
      BLOCKED: [ "bad", "!", "Not approved", "We could not verify it was you. Your bank has been notified. If you did not start this transaction, contact your bank now." ],
      MANUAL_REVIEW: [ "warn", "…", "Referred for review", "Your bank will review this transaction and may contact you." ],
      EXPIRED: [ "warn", "⧗", "Expired", "This approval timed out. Return to your bank and start again." ]
    }[st.status] || [ "warn", "?", st.status, "" ];
    clear(panel).appendChild(el("div", {
      class: "result " + map[0]
    }, [ el("div", {
      class: "icon",
      "aria-hidden": "true",
      text: map[1]
    }), el("h2", {
      text: map[2]
    }), el("p", {
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
    var input = el("input", {
      type: "tel",
      class: "code",
      id: "code",
      inputmode: "numeric",
      maxlength: "6",
      autocomplete: "one-time-code",
      "aria-describedby": "otp-help"
    });
    var btn = el("button", {
      type: "submit",
      text: "Verify code"
    });
    var form = el("form", {}, [ el("h2", {
      text: "Enter your code"
    }), el("p", {
      class: "muted",
      id: "otp-help",
      text: totp ? "Open your authenticator app and enter the 6-digit code for this bank." : "We sent a 6-digit code to " + ch.params.sentTo + ". The message names this amount and payee — check they match."
    }), el("label", {
      for: "code",
      text: "6-digit code"
    }), input, btn ]);
    form.addEventListener("submit", function(e) {
      e.preventDefault();
      var code = input.value.replace(/\D/g, "");
      if (code.length !== 6) {
        input.focus();
        return;
      }
      submit(ch, {
        code: code
      }, btn);
    });
    panel.appendChild(form);
    input.focus();
  }
  function faceStep(ch) {
    var dir = ch.params.action === "TURN_LEFT" ? "LEFT" : "RIGHT";
    var video = el("video", {
      autoplay: true,
      muted: true,
      playsinline: true,
      "aria-label": "Camera preview"
    });
    var prompt = el("div", {
      class: "prompt",
      text: "Look straight at the camera"
    });
    var btn = el("button", {
      type: "button",
      text: "Start face check"
    });
    panel.appendChild(el("h2", {
      text: "Face check"
    }));
    panel.appendChild(el("p", {
      class: "muted",
      text: "Keep your face inside the oval. When asked, turn your head to your " + dir.toLowerCase() + " (about a quarter turn) and hold it until the check finishes."
    }));
    panel.appendChild(el("div", {
      class: "camera"
    }, [ video, el("div", {
      class: "oval"
    }), prompt ]));
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
      var frames = [], interval = ch.params.intervalMs || 150;
      AMFA.sleep(600).then(function() {
        return camera.burst(3, interval);
      }).then(function(f) {
        frames = frames.concat(f);
        prompt.textContent = "Now slowly turn your head to your " + dir.toLowerCase() + " and hold it";
        return camera.burst(Math.max(3, (ch.params.frames || 12) - 3), interval);
      }).then(function(f) {
        prompt.textContent = "Done — checking";
        submit(ch, {
          frames: frames.concat(f)
        }, btn);
      });
    });
  }
  function voiceStep(ch) {
    var minS = ch.params.minSeconds || 3, maxS = ch.params.maxSeconds || 10;
    var meter = el("span"), btn = el("button", {
      type: "button",
      text: "Start recording"
    });
    var status = el("p", {
      class: "muted",
      text: "Tap start, then read the words aloud in your normal voice."
    });
    panel.appendChild(el("h2", {
      text: "Voice check"
    }));
    panel.appendChild(el("p", {
      class: "muted",
      text: "Read these words aloud:"
    }));
    panel.appendChild(el("div", {
      class: "prompt-phrase",
      text: ch.params.prompt
    }));
    panel.appendChild(el("div", {
      class: "meter",
      "aria-hidden": "true"
    }, [ meter ]));
    panel.appendChild(status);
    panel.appendChild(btn);
    var recording = false, started = 0, timer = null;
    function finish() {
      clearInterval(timer);
      recording = false;
      var out = mic.stop();
      mic = null;
      if (out.seconds < minS) {
        status.textContent = "That was too short. Please read all the words.";
        UI.busy(btn, false, "Start recording");
        return;
      }
      submit(ch, {
        audio: out.wav
      }, btn);
    }
    btn.addEventListener("click", function() {
      if (recording) {
        if ((performance.now() - started) / 1e3 >= minS) finish();
        return;
      }
      mic = new AMFA.Microphone;
      mic.start().then(function() {
        recording = true;
        started = performance.now();
        btn.textContent = "Stop";
        status.textContent = "Recording…";
        timer = setInterval(function() {
          meter.style.width = Math.min(100, mic ? mic.level() * 400 : 0) + "%";
          if ((performance.now() - started) / 1e3 >= maxS) finish();
        }, 80);
      }).catch(function(err) {
        mic = null;
        status.textContent = UI.mediaErrorText(err, "microphone");
        btn.textContent = "Try again";
      });
    });
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
      text: "Typing check"
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
      requestAnimationFrame(function() {
        tracer = new AMFA.ShapeTracer(canvas, ch.params.shape, function(pts) {
          trace = pts;
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
      var payload = {
        keystrokeSample: keys.take()
      };
      if (trace) payload.shapeTrace = trace;
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
