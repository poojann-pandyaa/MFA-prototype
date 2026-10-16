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
