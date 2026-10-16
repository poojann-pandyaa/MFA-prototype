(function(global) {
  "use strict";
  var DB_NAME = "amfa-device", STORE = "keys";
  function scope() {
    var t = null;
    try {
      t = new URLSearchParams(global.location.search).get("t");
    } catch (e) {
      t = null;
    }
    return t && /^[a-z0-9-]{2,48}$/.test(t) ? t : "default";
  }
  var KEY_ID = "device-key-v1:" + scope(), ID_KEY = "amfa.deviceId." + scope();
  function b64url(bytes) {
    var s = "", a = new Uint8Array(bytes);
    for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function b64(bytes) {
    var s = "", a = new Uint8Array(bytes), chunk = 32768;
    for (var i = 0; i < a.length; i += chunk) s += String.fromCharCode.apply(null, a.subarray(i, i + chunk));
    return btoa(s);
  }
  function randomId(prefix) {
    var a = new Uint8Array(18);
    crypto.getRandomValues(a);
    return prefix + b64url(a);
  }
  function sleep(ms) {
    return new Promise(function(r) {
      setTimeout(r, ms);
    });
  }
  function ApiError(status, code, message, fields) {
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.message = message || code;
    this.fields = fields;
  }
  ApiError.prototype = Object.create(Error.prototype);
  function Client(baseUrl, token) {
    this.base = (baseUrl || "").replace(/\/$/, "");
    var t = token;
    this._auth = function() {
      return "Bearer " + t;
    };
  }
  Client.prototype.request = function(method, path, body) {
    var opts = {
      method: method,
      headers: {
        authorization: this._auth()
      },
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer"
    };
    if (body !== undefined) {
      opts.headers["content-type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(this.base + path, opts).then(function(r) {
      return r.text().then(function(text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (e) {
          data = null;
        }
        if (!r.ok) {
          var err = data && data.error || {};
          throw new ApiError(r.status, err.code || "HTTP_" + r.status, err.message, err.fields);
        }
        return data;
      });
    });
  };
  Client.prototype.get = function(p) {
    return this.request("GET", p);
  };
  Client.prototype.post = function(p, b) {
    return this.request("POST", p, b === undefined ? {} : b);
  };
  function takeTokenFromFragment() {
    var h = global.location.hash || "";
    var m = /[#&]token=([A-Za-z0-9_-]+)/.exec(h);
    if (global.history && global.history.replaceState) {
      global.history.replaceState(null, "", global.location.pathname + global.location.search);
    }
    return m ? m[1] : null;
  }
  function idb() {
    return new Promise(function(resolve, reject) {
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function() {
        req.result.createObjectStore(STORE);
      };
      req.onsuccess = function() {
        resolve(req.result);
      };
      req.onerror = function() {
        reject(req.error);
      };
    });
  }
  function idbGet(key) {
    return idb().then(function(db) {
      return new Promise(function(resolve, reject) {
        var tx = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
        tx.onsuccess = function() {
          resolve(tx.result);
        };
        tx.onerror = function() {
          reject(tx.error);
        };
      });
    });
  }
  function idbPut(key, value) {
    return idb().then(function(db) {
      return new Promise(function(resolve, reject) {
        var tx = db.transaction(STORE, "readwrite").objectStore(STORE).put(value, key);
        tx.onsuccess = function() {
          resolve();
        };
        tx.onerror = function() {
          reject(tx.error);
        };
      });
    });
  }
  function deviceId() {
    var id = null;
    try {
      id = global.localStorage.getItem(ID_KEY);
    } catch (e) {
      id = null;
    }
    if (!id) {
      id = randomId("dev_");
      try {
        global.localStorage.setItem(ID_KEY, id);
      } catch (e) {}
    }
    return id;
  }
  function deviceKey() {
    if (!global.crypto || !crypto.subtle || !global.indexedDB) {
      return Promise.reject(new Error("device keys need WebCrypto and IndexedDB"));
    }
    return idbGet(KEY_ID).then(function(pair) {
      if (pair) return pair;
      return crypto.subtle.generateKey({
        name: "ECDSA",
        namedCurve: "P-256"
      }, false, [ "sign", "verify" ]).then(function(kp) {
        return idbPut(KEY_ID, kp).then(function() {
          return kp;
        });
      });
    }).then(function(kp) {
      return crypto.subtle.exportKey("jwk", kp.publicKey).then(function(jwk) {
        return {
          deviceId: deviceId(),
          publicJwk: {
            kty: jwk.kty,
            crv: jwk.crv,
            x: jwk.x,
            y: jwk.y
          }
        };
      });
    });
  }
  function hasDeviceKey() {
    try {
      return idbGet(KEY_ID).then(function(k) {
        return !!k;
      }).catch(function() {
        return false;
      });
    } catch (e) {
      return Promise.resolve(false);
    }
  }
  function signWithDevice(message) {
    return idbGet(KEY_ID).then(function(kp) {
      if (!kp) throw new Error("no device key on this browser");
      return crypto.subtle.sign({
        name: "ECDSA",
        hash: "SHA-256"
      }, kp.privateKey, (new TextEncoder).encode(message));
    }).then(b64url);
  }
  function capabilities() {
    var pointer = !!(global.matchMedia && global.matchMedia("(pointer: fine)").matches);
    var base = {
      camera: false,
      microphone: false,
      pointer: pointer
    };
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return Promise.resolve(base);
    return navigator.mediaDevices.enumerateDevices().then(function(list) {
      list.forEach(function(d) {
        if (d.kind === "videoinput") base.camera = true;
        if (d.kind === "audioinput") base.microphone = true;
      });
      return base;
    }).catch(function() {
      return base;
    });
  }
  function Telemetry() {
    this.start = Date.now() / 1e3;
    this.actions = [];
    var self = this, mark = function() {
      if (self.actions.length < 2e3) self.actions.push(Date.now() / 1e3);
    };
    this._mark = mark;
    document.addEventListener("click", mark, true);
    document.addEventListener("keydown", mark, true);
  }
  Telemetry.prototype.payload = function() {
    return {
      start: this.start,
      end: Date.now() / 1e3,
      actions: this.actions.slice()
    };
  };
  Telemetry.prototype.stop = function() {
    document.removeEventListener("click", this._mark, true);
    document.removeEventListener("keydown", this._mark, true);
  };
  function KeystrokeRecorder(input) {
    this.input = input;
    this.events = [];
    this.down = {};
    var self = this;
    this._kd = function(e) {
      if (e.repeat) return;
      self.down[e.key] = performance.now();
    };
    this._ku = function(e) {
      var t0 = self.down[e.key];
      if (t0 === undefined) return;
      delete self.down[e.key];
      if (self.events.length < 400) self.events.push({
        key: e.key,
        down: Math.round(t0 * 100) / 100,
        up: Math.round(performance.now() * 100) / 100
      });
    };
    this._paste = function(e) {
      e.preventDefault();
    };
    input.addEventListener("keydown", this._kd);
    input.addEventListener("keyup", this._ku);
    input.addEventListener("paste", this._paste);
    input.addEventListener("drop", this._paste);
    input.setAttribute("autocomplete", "off");
    input.setAttribute("autocorrect", "off");
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("spellcheck", "false");
  }
  KeystrokeRecorder.prototype.take = function() {
    var out = this.events.slice();
    this.events = [];
    this.down = {};
    return out;
  };
  KeystrokeRecorder.prototype.detach = function() {
    this.input.removeEventListener("keydown", this._kd);
    this.input.removeEventListener("keyup", this._ku);
    this.input.removeEventListener("paste", this._paste);
    this.input.removeEventListener("drop", this._paste);
  };
  function ShapeTracer(canvas, shape, onDone) {
    this.canvas = canvas;
    this.shape = shape;
    this.onDone = onDone;
    this.points = [];
    this.active = false;
    var self = this;
    this._resize();
    this._down = function(e) {
      if (!self._nearStart(e)) return;
      self.active = true;
      self.points = [];
      canvas.setPointerCapture(e.pointerId);
      self._add(e);
      e.preventDefault();
    };
    this._move = function(e) {
      if (self.active) {
        self._add(e);
        self.draw();
      }
    };
    this._up = function() {
      if (!self.active) return;
      self.active = false;
      var pts = self.points.slice();
      self.draw();
      if (self.onDone) self.onDone(pts);
    };
    canvas.addEventListener("pointerdown", this._down);
    canvas.addEventListener("pointermove", this._move);
    canvas.addEventListener("pointerup", this._up);
    canvas.addEventListener("pointercancel", this._up);
    canvas.classList.add("amfa-shape");
    this.draw();
  }
  ShapeTracer.prototype._resize = function() {
    var r = this.canvas.getBoundingClientRect(), dpr = global.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
  };
  ShapeTracer.prototype._norm = function(e) {
    var r = this.canvas.getBoundingClientRect();
    return {
      x: (e.clientX - r.left) / r.width,
      y: (e.clientY - r.top) / r.height
    };
  };
  ShapeTracer.prototype._nearStart = function(e) {
    var p = this._norm(e), s = this.shape[0];
    return Math.hypot(p.x - s[0], p.y - s[1]) < .12;
  };
  ShapeTracer.prototype._add = function(e) {
    var p = this._norm(e);
    if (this.points.length < 5e3) this.points.push({
      x: Math.round(p.x * 1e4) / 1e4,
      y: Math.round(p.y * 1e4) / 1e4,
      t: Math.round(performance.now() * 100) / 100
    });
  };
  ShapeTracer.prototype.draw = function() {
    var c = this.canvas, g = c.getContext("2d"), w = c.width, h = c.height, s = this.shape;
    var css = getComputedStyle(c);
    g.clearRect(0, 0, w, h);
    g.lineWidth = Math.max(10, w * .02);
    g.lineCap = "round";
    g.lineJoin = "round";
    g.strokeStyle = css.getPropertyValue("--amfa-shape-guide") || "#c9d3e3";
    g.beginPath();
    s.forEach(function(pt, i) {
      (i ? g.lineTo : g.moveTo).call(g, pt[0] * w, pt[1] * h);
    });
    g.stroke();
    g.fillStyle = css.getPropertyValue("--amfa-shape-start") || "#1f7a4d";
    g.beginPath();
    g.arc(s[0][0] * w, s[0][1] * h, Math.max(9, w * .018), 0, Math.PI * 2);
    g.fill();
    g.fillStyle = css.getPropertyValue("--amfa-shape-end") || "#b3261e";
    var e = s[s.length - 1];
    g.beginPath();
    g.arc(e[0] * w, e[1] * h, Math.max(9, w * .018), 0, Math.PI * 2);
    g.fill();
    if (this.points.length > 1) {
      g.lineWidth = Math.max(3, w * .006);
      g.strokeStyle = css.getPropertyValue("--amfa-shape-ink") || "#1b4fd8";
      g.beginPath();
      this.points.forEach(function(p, i) {
        (i ? g.lineTo : g.moveTo).call(g, p.x * w, p.y * h);
      });
      g.stroke();
    }
  };
  ShapeTracer.prototype.detach = function() {
    this.canvas.removeEventListener("pointerdown", this._down);
    this.canvas.removeEventListener("pointermove", this._move);
    this.canvas.removeEventListener("pointerup", this._up);
    this.canvas.removeEventListener("pointercancel", this._up);
  };
  function mediaError(name, message) {
    var e = new Error(message || name);
    e.name = name;
    return e;
  }
  function policyAllows(feature) {
    var p = document.permissionsPolicy || document.featurePolicy;
    try {
      return !p || !p.allowsFeature || p.allowsFeature(feature);
    } catch (e) {
      return true;
    }
  }
  function preflight(feature) {
    if (!global.isSecureContext) return Promise.reject(mediaError("InsecureContext"));
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return Promise.reject(mediaError("Unsupported"));
    if (!policyAllows(feature)) return Promise.reject(mediaError("PolicyBlocked"));
    return Promise.resolve();
  }
  var FINAL = {
    NotAllowedError: 1,
    SecurityError: 1,
    PermissionDeniedError: 1
  };
  function openMedia(list) {
    var i = 0, lastErr = null;
    function attempt() {
      if (i >= list.length) return Promise.reject(lastErr || mediaError("NotReadableError"));
      return navigator.mediaDevices.getUserMedia(list[i++]).catch(function(err) {
        lastErr = err;
        if (FINAL[err && err.name]) throw err;
        return attempt();
      });
    }
    return attempt();
  }
  function listCameras() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return Promise.resolve([]);
    return navigator.mediaDevices.enumerateDevices().then(function(ds) {
      return ds.filter(function(d) {
        return d.kind === "videoinput";
      }).map(function(d, i) {
        return {
          id: d.deviceId,
          label: d.label || "Camera " + (i + 1)
        };
      });
    }, function() {
      return [];
    });
  }
  function Camera(video) {
    this.video = video;
    this.stream = null;
    this.deviceId = null;
  }
  Camera.prototype.start = function(deviceId) {
    var self = this, size = {
      width: {
        ideal: 960
      },
      height: {
        ideal: 720
      }
    };
    self.stop();
    var sets = deviceId ? [ {
      video: Object.assign({
        deviceId: {
          exact: deviceId
        }
      }, size),
      audio: false
    }, {
      video: {
        deviceId: {
          exact: deviceId
        }
      },
      audio: false
    } ] : [ {
      video: Object.assign({
        facingMode: "user"
      }, size),
      audio: false
    }, {
      video: true,
      audio: false
    } ];
    return preflight("camera").then(function() {
      return openMedia(sets);
    }).then(function(stream) {
      self.stream = stream;
      var track = stream.getVideoTracks()[0];
      self.deviceId = track && track.getSettings ? track.getSettings().deviceId || null : null;
      self.video.setAttribute("playsinline", "");
      self.video.muted = true;
      self.video.srcObject = stream;
      return Promise.resolve(self.video.play()).catch(function() {});
    }).then(function() {
      return self._waitForPicture();
    });
  };
  Camera.prototype._waitForPicture = function() {
    var self = this, started = Date.now();
    return new Promise(function(resolve, reject) {
      (function poll() {
        if (!self.stream) return reject(mediaError("AbortError"));
        var v = self.video;
        if (v.videoWidth > 0 && v.readyState >= 2) {
          return sleep(600).then(function() {
            if (self.brightness() < 6) reject(mediaError("BlackImage")); else resolve();
          });
        }
        if (Date.now() - started > 8e3) return reject(mediaError("NoPicture"));
        setTimeout(poll, 150);
      })();
    });
  };
  Camera.prototype.brightness = function() {
    var v = this.video;
    if (!v.videoWidth) return 0;
    var c = document.createElement("canvas");
    c.width = 32;
    c.height = 24;
    var g = c.getContext("2d");
    g.drawImage(v, 0, 0, 32, 24);
    var d = g.getImageData(0, 0, 32, 24).data, sum = 0;
    for (var i = 0; i < d.length; i += 4) sum += .299 * d[i] + .587 * d[i + 1] + .114 * d[i + 2];
    return sum / (d.length / 4);
  };
  Camera.prototype.frame = function() {
    var v = this.video, c = document.createElement("canvas");
    var scale = Math.min(1, 960 / Math.max(v.videoWidth, v.videoHeight));
    c.width = Math.round(v.videoWidth * scale);
    c.height = Math.round(v.videoHeight * scale);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", .92).split(",")[1];
  };
  // onTick(i) runs before frame i; it may return a number of ms to wait before that frame is taken,
  // so a customer shown a new cue has time to follow it before the picture.
  // Many phone cameras slow to 7-10 frames a second in ordinary light, so two captures can land on the
  // same camera frame. The face service rejects a burst with repeated frames as a replay attack (and locks
  // the customer), so never send a repeat: wait for the camera's next frame. A camera that stops sending
  // new pictures gives a CameraStalled error the page can show, instead of a burst that looks like an attack.
  Camera.prototype.burst = function(n, intervalMs, onTick) {
    var self = this, frames = [], last = null;
    function nextVideoFrame() {
      var v = self.video;
      return new Promise(function(r) {
        var done = false, go = function() {
          if (!done) {
            done = true;
            r();
          }
        };
        if (v && v.requestVideoFrameCallback) v.requestVideoFrameCallback(go);
        setTimeout(go, v && v.requestVideoFrameCallback ? 120 : 40);
      });
    }
    function fresh() {
      var deadline = Date.now() + 1500;
      return function attempt() {
        var f = self.frame();
        if (f !== last) return Promise.resolve(f);
        if (Date.now() > deadline) return Promise.reject(mediaError("CameraStalled"));
        return nextVideoFrame().then(attempt);
      }();
    }
    function step(i) {
      if (i >= n) return Promise.resolve(frames);
      var lead = onTick ? onTick(i) : 0;
      return sleep(lead || 0).then(fresh).then(function(f) {
        last = f;
        frames.push(f);
        return i + 1 < n ? sleep(intervalMs) : null;
      }).then(function() {
        return step(i + 1);
      });
    }
    return step(0);
  };
  Camera.prototype.stop = function() {
    if (this.stream) this.stream.getTracks().forEach(function(t) {
      t.stop();
    });
    this.stream = null;
    this.video.srcObject = null;
  };
  function Microphone() {
    this.ctx = null;
    this.stream = null;
    this.chunks = [];
    this.node = null;
  }
  Microphone.prototype.start = function() {
    var self = this;
    return preflight("microphone").then(function() {
      return openMedia([ {
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: true
        }
      }, {
        audio: true
      } ]);
    }).then(function(stream) {
      self.stream = stream;
      var AC = global.AudioContext || global.webkitAudioContext;
      self.ctx = new AC;
      self.chunks = [];
      var src = self.ctx.createMediaStreamSource(stream);
      if (self.ctx.audioWorklet) {
        return self.ctx.audioWorklet.addModule("/static/js/pcm-worklet.js").then(function() {
          self.node = new AudioWorkletNode(self.ctx, "amfa-pcm");
          self.node.port.onmessage = function(e) {
            self.chunks.push(e.data);
          };
          src.connect(self.node);
        });
      }
      var proc = self.ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = function(e) {
        self.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      src.connect(proc);
      proc.connect(self.ctx.destination);
      self.node = proc;
    });
  };
  Microphone.prototype.level = function() {
    var last = this.chunks[this.chunks.length - 1];
    if (!last) return 0;
    var s = 0;
    for (var i = 0; i < last.length; i++) s += last[i] * last[i];
    return Math.sqrt(s / last.length);
  };
  Microphone.prototype.stop = function() {
    var rate = this.ctx ? this.ctx.sampleRate : 48e3, chunks = this.chunks;
    if (this.node) try {
      this.node.disconnect();
    } catch (e) {}
    if (this.stream) this.stream.getTracks().forEach(function(t) {
      t.stop();
    });
    if (this.ctx) this.ctx.close();
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.chunks = [];
    var total = chunks.reduce(function(n, c) {
      return n + c.length;
    }, 0), pcm = new Float32Array(total), o = 0;
    chunks.forEach(function(c) {
      pcm.set(c, o);
      o += c.length;
    });
    return {
      wav: encodeWav(resample(pcm, rate, 16e3), 16e3),
      seconds: total / rate
    };
  };
  function resample(input, from, to) {
    if (from === to) return input;
    var ratio = from / to, n = Math.floor(input.length / ratio), out = new Float32Array(n);
    for (var i = 0; i < n; i++) {
      var start = Math.floor(i * ratio), end = Math.min(input.length, Math.floor((i + 1) * ratio)), s = 0;
      for (var j = start; j < end; j++) s += input[j];
      out[i] = s / Math.max(1, end - start);
    }
    return out;
  }
  function encodeWav(samples, rate) {
    var buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
    function str(off, s) {
      for (var i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i));
    }
    str(0, "RIFF");
    v.setUint32(4, 36 + samples.length * 2, true);
    str(8, "WAVE");
    str(12, "fmt ");
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);
    v.setUint16(22, 1, true);
    v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    str(36, "data");
    v.setUint32(40, samples.length * 2, true);
    for (var i = 0; i < samples.length; i++) {
      var x = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(44 + i * 2, x < 0 ? x * 32768 : x * 32767, true);
    }
    return b64(buf);
  }
  global.AMFA = {
    version: "1.0.0",
    Client: Client,
    ApiError: ApiError,
    takeTokenFromFragment: takeTokenFromFragment,
    deviceId: deviceId,
    deviceKey: deviceKey,
    hasDeviceKey: hasDeviceKey,
    signWithDevice: signWithDevice,
    capabilities: capabilities,
    Telemetry: Telemetry,
    KeystrokeRecorder: KeystrokeRecorder,
    ShapeTracer: ShapeTracer,
    Camera: Camera,
    Microphone: Microphone,
    listCameras: listCameras,
    sleep: sleep
  };
})(window);
