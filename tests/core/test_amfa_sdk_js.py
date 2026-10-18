"""AMFA's browser SDK (amfa.js), run with Node against a fake camera (skipped where Node isn't installed)."""
import shutil
import subprocess
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[2] / "amfa" / "core" / "static" / "sdk" / "amfa.js"

CHECK = """
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
globalThis.window = globalThis;
new Function(readFileSync(%s, "utf8"))();
const cam = (shots) => {   // a camera whose next pictures are `shots`, then the last one forever (frozen)
  const c = new window.AMFA.Camera({});
  let i = 0;
  c.frame = () => shots[Math.min(i++, shots.length - 1)];
  return c;
};
// a phone camera at 7 fps hands the same picture to two captures in a row: never send a repeat
const frames = await cam(["a", "a", "b", "b", "b", "c", "d"]).burst(4, 0);
assert.deepEqual(frames, ["a", "b", "c", "d"]);
// a camera that stops: a clear error the page can show, not a burst of identical frames
await assert.rejects(cam(["x", "y"]).burst(4, 0), (e) => e.name === "CameraStalled");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_burst_never_repeats_a_frame_and_a_frozen_camera_is_reported():
    r = subprocess.run(["node", "--input-type=module", "-e", CHECK % repr(str(SDK))],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]


TYPING = """
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
globalThis.window = globalThis;
new Function(readFileSync(%s, "utf8"))();
let T = 0;
globalThis.performance = { now: () => T };
const box = () => {   // a text box with just the listener API the recorder uses
  const on = {};
  return { value: "", setAttribute() {}, addEventListener: (k, f) => (on[k] = f),
           removeEventListener: () => {}, fire: (k, e) => on[k] && on[k](e) };
};
const phrase = "the quick brown fox 2026";

// An Android keyboard: every letter arrives as key "Unidentified" (keyCode 229); the letter is in the
// text box. A key that types nothing (switching to the number keyboard) must not take the next letter.
const b = box(), rec = new window.AMFA.KeystrokeRecorder(b), truth = [];
const press = (ch) => {
  T += 150;
  b.fire("keydown", { key: "Unidentified", keyCode: 229 });
  const at = T;
  if (ch !== null) { b.value += ch; b.fire("input", {}); truth.push([ch, at]); }
  T += 3;
  b.fire("keyup", { key: "Unidentified", keyCode: 229 });
};
for (const ch of "the quick brown fox ") press(ch);
press(null);
for (const ch of "2026") press(ch);
const got = rec.take();
assert.equal(got.length, phrase.length);
got.forEach((e, i) => { assert.equal(e.key, truth[i][0]); assert.equal(e.down, truth[i][1]); });

// a corrected typo arrives as Backspace, like on a computer
const b2 = box(), r2 = new window.AMFA.KeystrokeRecorder(b2);
const tap = (v) => { T += 150; b2.fire("keydown", { keyCode: 229, key: "Unidentified" }); b2.value = v;
                     b2.fire("input", {}); T += 3; b2.fire("keyup", { keyCode: 229, key: "Unidentified" }); };
tap("t"); tap("tx"); tap("t"); tap("th");
assert.deepEqual(r2.take().map((e) => e.key), ["t", "x", "Backspace", "h"]);

// several samples in a row: the page clears the box after taking each one, without an input event
const b4 = box(), r4 = new window.AMFA.KeystrokeRecorder(b4);
for (let round = 0; round < 3; round++) {
  for (const ch of "the quick") {
    T += 150; b4.fire("keydown", { key: "Unidentified", keyCode: 229 });
    b4.value += ch; b4.fire("input", {});
    T += 3; b4.fire("keyup", { key: "Unidentified", keyCode: 229 });
  }
  assert.equal(r4.take().map((e) => e.key).join(""), "the quick");
  b4.value = "";
}

// a computer keyboard is unchanged: named keys, real hold times
const b3 = box(), r3 = new window.AMFA.KeystrokeRecorder(b3);
for (const ch of "ab") { T += 100; b3.fire("keydown", { key: ch }); b3.value += ch; b3.fire("input", {});
                         T += 80; b3.fire("keyup", { key: ch }); }
assert.deepEqual(r3.take().map((e) => [e.key, e.up - e.down]), [["a", 80], ["b", 80]]);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_phone_keyboards_that_hide_the_key_are_still_recorded():
    r = subprocess.run(["node", "--input-type=module", "-e", TYPING % repr(str(SDK))],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
