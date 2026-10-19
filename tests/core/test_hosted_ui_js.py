"""The hosted pages' shared UI helpers (ui.js), run with Node (skipped where Node isn't installed)."""
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "amfa" / "core" / "static" / "js" / "ui.js"

CHECK = """
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
globalThis.window = globalThis;
globalThis.location = { search: "", hash: "" };
let now = 1000, timers = [];
Date.now = () => now * 1000;
globalThis.setTimeout = (fn) => timers.push(fn);
const tick = () => { now += 1; const due = timers; timers = []; due.forEach((f) => f()); };
new Function(readFileSync(%s, "utf8"))();

// the page starts the timer before the form is on screen: it must keep counting once it is
const node = { isConnected: false, textContent: "" };
let done = 0;
window.UI.countdown(node, now + 62, "Enter it within ", () => done++);
assert.equal(node.textContent, "Enter it within 1:02");
node.isConnected = true;
tick();
assert.equal(node.textContent, "Enter it within 1:01");
for (let i = 0; i < 61; i++) tick();
assert.equal(node.textContent, "This code has expired. Ask for a new one.");
assert.equal(done, 1);
assert.equal(timers.length, 0);

// a screen that was shown and then replaced stops its timer
const gone = { isConnected: true, textContent: "" };
window.UI.countdown(gone, now + 30, "");
gone.isConnected = false;
tick();
assert.equal(timers.length, 0);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_code_timer_counts_down_even_when_started_before_it_is_shown():
    r = subprocess.run(["node", "--input-type=module", "-e", CHECK % repr(str(UI))],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]


DOM = """
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.listeners = {};
    this.textContent = ""; this.className = ""; this.disabled = false;
    const self = this;
    this.classList = {
      add: (...c) => { const s = new Set(self.className.split(" ").filter(Boolean)); c.forEach((x) => s.add(x)); self.className = [...s].join(" "); },
      remove: (...c) => { self.className = self.className.split(" ").filter((x) => x && !c.includes(x)).join(" "); },
      contains: (c) => self.className.split(" ").includes(c),
      toggle: (c, on) => on ? self.classList.add(c) : self.classList.remove(c) };
  }
  appendChild(c) { this.children.push(c); return c; }
  get lastChild() { return this.children[this.children.length - 1]; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t, e = {}) { (this.listeners[t] || []).forEach((f) => f(Object.assign({ preventDefault() {}, button: 0, pointerId: 1 }, e))); }
  setPointerCapture() {}
}
globalThis.window = globalThis;
globalThis.location = { search: "", hash: "" };
globalThis.document = { createElement: (t) => new Node(t), createElementNS: (_, t) => new Node(t), documentElement: new Node("html") };
let now = 0, intervals = [];
globalThis.performance = { now: () => now };
globalThis.setInterval = (f) => { intervals.push(f); return intervals.length; };
globalThis.clearInterval = (id) => { intervals[id - 1] = null; };
const advance = (ms) => { for (let t = 0; t < ms; t += 80) { now += 80; intervals.forEach((f) => f && f()); } };
const flush = () => new Promise((r) => setTimeout(r, 0));
let started = 0, stopped = 0, resolveStart = null;
globalThis.AMFA = { Microphone: class {
  start() { started++; return new Promise((r) => { resolveStart = r; }); }
  stop() { stopped++; return { wav: "WAV", seconds: now / 1000 }; }
  level() { return 0.1; } } };
new Function(readFileSync(__UI__, "utf8"))();
const UI = window.UI;
"""

STEP_AND_TALK = DOM + """
// the per-step bar says how far through the step the customer is
const p = UI.stepProgress("Sentences");
p.set(1, 3);
assert.equal(p.node.children[0].children[1].textContent, "1 of 3");
assert.equal(p.node.lastChild.children[0].style.width, "33%");
assert.equal(p.node.lastChild.getAttribute("aria-valuenow"), "1");
p.set(3, 3);
assert.ok(p.node.classList.contains("complete"));

// the banner names the flow
assert.equal(UI.modeBanner("enroll", "x").children[0].textContent, "One-time setup");
assert.equal(UI.modeBanner("verify", "x").children[0].textContent, "Verification");

let recorded = [];
const talk = UI.holdToTalk({ minSeconds: 2, maxSeconds: 6, onRecorded: (o) => recorded.push(o) });
const hint = () => talk.node.children[2].textContent;

// let go while the browser is still asking for the microphone: nothing is kept, and it says it's ready
talk.button.fire("pointerdown");
talk.button.fire("pointerup");
resolveStart(); await flush();
assert.equal(recorded.length, 0);
assert.equal(stopped, 1);
assert.match(hint(), /ready/i);

// too short: discarded, asked to hold longer
talk.button.fire("pointerdown"); resolveStart(); await flush();
advance(800);
talk.button.fire("pointerup");
assert.equal(recorded.length, 0);
assert.match(hint(), /too short/i);

// held long enough: recorded on release, once (pointerup and lostpointercapture both fire)
now = 0; talk.button.fire("pointerdown"); resolveStart(); await flush();
advance(2400);
assert.ok(talk.node.classList.contains("enough"));
talk.button.fire("pointerup");
talk.button.fire("lostpointercapture");
assert.equal(recorded.length, 1);

// the keyboard works the same way (Space held, then released), and the maximum stops it by itself
talk.reset();
now = 0; talk.button.fire("keydown", { key: " ", repeat: false }); resolveStart(); await flush();
advance(6100);
assert.equal(recorded.length, 2);
talk.button.fire("keyup", { key: " " });
assert.equal(recorded.length, 2);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_step_progress_mode_banner_and_hold_to_talk():
    r = subprocess.run(["node", "--input-type=module", "-e", STEP_AND_TALK.replace("__UI__", repr(str(UI)))],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
