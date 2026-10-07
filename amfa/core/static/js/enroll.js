(function() {
  "use strict";
  var el = UI.el, clear = UI.clear;
  var token = AMFA.takeTokenFromFragment();
  var api = token ? new AMFA.Client("", token) : null;
  var panel = document.getElementById("panel");
  var state = null, steps = [], index = 0, cleanup = [];
  var SENTENCES = [ "My voice confirms my identity for my bank.", "Seven orange rivers flow past the quiet mountain.", "I approve payments only when I start them myself." ];
  function done() {
    cleanup.forEach(function(f) {
      try {
        f();
      } catch (e) {}
    });
    cleanup = [];
  }
  function progress() {
    var pct = steps.length ? Math.round(100 * index / steps.length) : 0;
    document.getElementById("progress").style.width = pct + "%";
  }
  function next() {
    done();
    index += 1;
    progress();
    run();
  }
  function fail(e) {
    done();
    clear(panel).appendChild(UI.notice("bad", UI.errorText(e)));
  }
  function rejected(res) {
    var reasons = {
      NO_FACE_DETECTED: "We couldn't see your face clearly. Face the camera in good light.",
      MULTIPLE_FACES: "Only you should be in view.",
      LIVENESS_NOT_ESTABLISHED: "We couldn't confirm a live camera image. Use good, even lighting and try again.",
      INCONSISTENT_IDENTITY: "Please keep your face in view for the whole capture.",
      NOT_ENOUGH_SPEECH: "Please read the whole sentence.",
      TOO_NOISY: "Find a quieter place and try again.",
      AUDIO_CLIPPED: "Hold the device a little further away.",
      INCONSISTENT_SPEAKER: "All three recordings need to be in your own voice.",
      NOT_ENOUGH_USABLE_SAMPLES: "Some typing samples didn't match the phrase exactly. Let's do a few more."
    };
    return reasons[res.reason] || "That didn't work (" + (res.reason || "error") + "). Please try again.";
  }
  function heading(t, sub) {
    clear(panel);
    panel.appendChild(el("h2", {
      text: t
    }));
    if (sub) panel.appendChild(el("p", {
      class: "muted",
      text: sub
    }));
  }
  function consentStep() {
    var c = state.consent;
    if (!c.required) return next();
    heading("Before we start", state.bank + " will use the following to confirm it's really you when you approve transactions:");
    var list = el("ul", {
      class: "consent"
    });
    var what = {
      OTP: "an authenticator app code",
      FACE: "a template of your face",
      VOICE: "a template of your voice",
      UEBA: "how you type a short phrase and trace a line"
    };
    state.requested.forEach(function(f) {
      list.appendChild(el("li", {
        text: what[f]
      }));
    });
    panel.appendChild(list);
    panel.appendChild(el("p", {
      class: "muted",
      text: "Purposes: " + c.purposes.join("; ") + "."
    }));
    panel.appendChild(el("p", {
      class: "muted",
      text: c.retention + "."
    }));
    var box = el("input", {
      type: "checkbox",
      id: "agree"
    });
    var btn = el("button", {
      type: "button",
      text: "I agree — continue",
      disabled: true
    });
    box.addEventListener("change", function() {
      btn.disabled = !box.checked;
    });
    panel.appendChild(el("label", {
      class: "check",
      for: "agree"
    }, [ box, el("span", {
      text: "I consent to this processing for these purposes. I can withdraw by asking " + state.bank + " to remove my templates."
    }) ]));
    panel.appendChild(btn);
    btn.addEventListener("click", function() {
      UI.busy(btn, true, "Saving…");
      api.post("/c/v1/enrollment/consent", {
        accepted: true,
        version: c.version
      }).then(next, fail);
    });
  }
  function otpStep() {
    heading("Link your authenticator app", "Scan this code with Google Authenticator, Microsoft Authenticator or any TOTP app.");
    api.post("/c/v1/enrollment/totp/start").then(function(t) {
      var img = el("img", {
        alt: "QR code for your authenticator app",
        src: "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(t.qrSvg)))
      });
      panel.appendChild(el("div", {
        class: "qr"
      }, [ img ]));
      panel.appendChild(el("p", {
        class: "muted",
        text: "Can't scan? Enter this key manually:"
      }));
      panel.appendChild(el("p", {
        class: "secret",
        text: t.secret
      }));
      var input = el("input", {
        type: "tel",
        class: "code",
        id: "code",
        inputmode: "numeric",
        maxlength: "6",
        autocomplete: "one-time-code"
      });
      var btn = el("button", {
        type: "submit",
        text: "Confirm"
      });
      var form = el("form", {}, [ el("label", {
        for: "code",
        text: "Enter the 6-digit code it shows"
      }), input, btn ]);
      panel.appendChild(form);
      form.addEventListener("submit", function(e) {
        e.preventDefault();
        UI.busy(btn, true, "Checking…");
        api.post("/c/v1/enrollment/totp/confirm", {
          code: input.value.replace(/\D/g, "")
        }).then(next, function(err) {
          UI.busy(btn, false, "Confirm");
          form.appendChild(UI.notice("bad", err.code === "OTP_INVALID" ? "That code isn't right. Wait for a new one and try again." : UI.errorText(err)));
        });
      });
    }, function(e) {
      if (e.code === "ALREADY_ENROLLED") return next();
      fail(e);
    });
  }
  function faceStep() {
    heading("Face", "Hold your device at eye level in good light. We'll take five quick pictures.");
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
      text: "Capture"
    });
    panel.appendChild(el("div", {
      class: "camera"
    }, [ video, el("div", {
      class: "oval"
    }), prompt ]));
    var cam = new AMFA.Camera(video);
    cleanup.push(function() {
      cam.stop();
    });
    btn.disabled = true;
    UI.setupCamera(cam, panel, function() {
      btn.disabled = false;
    }, function() {
      btn.disabled = true;
    });
    panel.appendChild(btn);
    var cues = [ "Look straight at the camera", "Look straight at the camera", "Tilt your head slightly left", "Tilt your head slightly right", "Look straight at the camera" ];
    btn.addEventListener("click", function() {
      UI.busy(btn, true, "Hold still…");
      AMFA.sleep(500).then(function() {
        return cam.burst(5, 450, function(i) {
          prompt.textContent = cues[i];
        });
      }).then(function(frames) {
        prompt.textContent = "Checking…";
        return api.post("/c/v1/enrollment/face", {
          frames: frames
        });
      }).then(function(res) {
        if (res.enrolled) return next();
        UI.busy(btn, false, "Try again");
        panel.appendChild(UI.notice("bad", rejected(res)));
        prompt.textContent = cues[0];
      }, fail);
    });
  }
  function voiceStep() {
    var clips = [], i = 0;
    heading("Voice", "Read three short sentences aloud in your normal voice.");
    var phrase = el("div", {
      class: "prompt-phrase"
    }), meter = el("span");
    var status = el("p", {
      class: "muted"
    }), btn = el("button", {
      type: "button",
      text: "Start recording"
    });
    panel.appendChild(phrase);
    panel.appendChild(el("div", {
      class: "meter",
      "aria-hidden": "true"
    }, [ meter ]));
    panel.appendChild(status);
    panel.appendChild(btn);
    var mic = null, recording = false, started = 0, timer = null;
    cleanup.push(function() {
      clearInterval(timer);
      if (mic) try {
        mic.stop();
      } catch (e) {}
    });
    function show() {
      phrase.textContent = SENTENCES[i];
      status.textContent = "Sentence " + (i + 1) + " of 3";
    }
    show();
    function stop() {
      clearInterval(timer);
      recording = false;
      var out = mic.stop();
      mic = null;
      btn.textContent = "Start recording";
      if (out.seconds < 2.5) {
        status.textContent = "That was too short. Please read the whole sentence.";
        return;
      }
      clips.push(out.wav);
      i += 1;
      if (i < SENTENCES.length) return show();
      UI.busy(btn, true, "Checking…");
      api.post("/c/v1/enrollment/voice", {
        samples: clips
      }).then(function(res) {
        if (res.enrolled) return next();
        clips = [];
        i = 0;
        show();
        UI.busy(btn, false, "Start again");
        panel.appendChild(UI.notice("bad", rejected(res)));
      }, fail);
    }
    btn.addEventListener("click", function() {
      if (recording) {
        if (performance.now() - started > 1500) stop();
        return;
      }
      mic = new AMFA.Microphone;
      mic.start().then(function() {
        recording = true;
        started = performance.now();
        btn.textContent = "Stop";
        timer = setInterval(function() {
          meter.style.width = Math.min(100, mic ? mic.level() * 400 : 0) + "%";
          if (performance.now() - started > 1e4) stop();
        }, 80);
      }, function(err) {
        mic = null;
        status.textContent = UI.mediaErrorText(err, "microphone");
        btn.textContent = "Try again";
      });
    });
  }
  function typingStep() {
    var info = state.behaviour, target = info.repetitions || 10, samples = [], traces = [];
    var wantShape = false;
    heading("Typing rhythm", "Type the phrase below " + target + " times, pressing Enter after each. " + "The first couple are practice — just type naturally.");
    var bar = el("span"), count = el("p", {
      class: "muted"
    });
    var input = el("input", {
      type: "text",
      class: "phrase",
      id: "phrase"
    });
    panel.appendChild(el("div", {
      class: "prompt-phrase",
      text: info.phrase
    }));
    panel.appendChild(el("div", {
      class: "progress",
      "aria-hidden": "true"
    }, [ bar ]));
    panel.appendChild(count);
    var form = el("form", {}, [ el("label", {
      for: "phrase",
      text: "Phrase"
    }), input ]);
    panel.appendChild(form);
    var rec = new AMFA.KeystrokeRecorder(input);
    cleanup.push(function() {
      rec.detach();
    });
    function update() {
      count.textContent = samples.length + " of " + target;
      bar.style.width = Math.round(100 * samples.length / target) + "%";
    }
    update();
    input.focus();
    AMFA.capabilities().then(function(c) {
      wantShape = c.pointer && info.shape;
    });
    form.addEventListener("submit", function(e) {
      e.preventDefault();
      var ev = rec.take();
      if (input.value === info.phrase) {
        samples.push(ev);
        update();
      }
      input.value = "";
      if (samples.length < target) return;
      rec.detach();
      if (wantShape) return shapeStage();
      send();
    });
    function shapeStage() {
      heading("Trace the line", "Drag from the green dot to the red dot, following the line. 5 times.");
      var canvas = el("canvas", {
        "aria-label": "Tracing area"
      }), c2 = el("p", {
        class: "muted",
        text: "0 of 5"
      });
      panel.appendChild(canvas);
      panel.appendChild(c2);
      requestAnimationFrame(function() {
        var tr = new AMFA.ShapeTracer(canvas, info.shape, function(pts) {
          if (pts.length < 40) return;
          traces.push(pts);
          c2.textContent = traces.length + " of 5";
          if (traces.length >= (info.shapeTracesRequired || 5)) {
            tr.detach();
            send();
          }
        });
        cleanup.push(function() {
          tr.detach();
        });
      });
    }
    function send() {
      heading("Typing rhythm", "Checking…");
      api.post("/c/v1/enrollment/behaviour", {
        keystrokeSamples: samples,
        shapeTraces: traces
      }).then(function(res) {
        if (res.enrolled) return next();
        panel.appendChild(UI.notice("bad", rejected(res)));
        var again = el("button", {
          type: "button",
          text: "Try again"
        });
        again.addEventListener("click", typingStep);
        panel.appendChild(again);
      }, fail);
    }
  }
  function deviceStep() {
    heading("Securing this device", "Creating a key that never leaves this browser…");
    AMFA.deviceKey().then(function(k) {
      return api.post("/c/v1/enrollment/device", {
        deviceId: k.deviceId,
        publicJwk: k.publicJwk
      });
    }).then(next, function(e) {
      if (!(e instanceof AMFA.ApiError)) return next();
      fail(e);
    });
  }
  function completeStep() {
    api.post("/c/v1/enrollment/complete").then(function(r) {
      document.getElementById("progress").style.width = "100%";
      clear(panel).appendChild(el("div", {
        class: "result ok"
      }, [ el("div", {
        class: "icon",
        "aria-hidden": "true",
        text: "✓"
      }), el("h2", {
        text: "You're set up"
      }), el("p", {
        class: "muted",
        text: "You can return to " + state.bank + ". You'll be asked for these checks only when a transaction needs them."
      }) ]));
      UI.postToParent({
        type: "amfa:enrolled",
        factors: r.enrolled
      });
    }, fail);
  }
  function run() {
    var s = steps[index];
    if (s) s();
  }
  if (!api) {
    clear(panel).appendChild(UI.notice("bad", "This link is incomplete. Ask your bank for a new one."));
    return;
  }
  api.get("/c/v1/enrollment").then(function(st) {
    state = st;
    document.getElementById("bank").textContent = st.bank;
    var order = {
      OTP: otpStep,
      FACE: faceStep,
      VOICE: voiceStep,
      UEBA: typingStep
    };
    steps = [ consentStep ];
    [ "OTP", "FACE", "VOICE", "UEBA" ].forEach(function(f) {
      var doneHere = (st.completedWithThisLink || []).indexOf(f) >= 0;
      if (st.requested.indexOf(f) >= 0 && !doneHere && (!st.enrolled[f] || st.replace)) steps.push(order[f]);
    });
    steps.push(deviceStep, completeStep);
    progress();
    run();
  }).catch(fail);
})();
