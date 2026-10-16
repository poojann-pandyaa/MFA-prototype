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
