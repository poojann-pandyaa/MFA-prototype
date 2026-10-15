"""The bank app's pure JavaScript helpers, run with Node (skipped where Node isn't installed)."""
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "examples" / "demo_bank" / "static" / "js" / "ui.js"

CHECK = """
import assert from "node:assert/strict";
import { press, rupees, shortName, STATUS } from "%s";
assert.equal(shortName("V. Mehta ••4411"), "Mehta");
assert.equal(shortName("City Electricity"), "City");
assert.equal(shortName("Ravi Kumar ••9012"), "Ravi");
const type = (keys) => keys.split("").reduce((t, k) => press(t, k === "<" ? "del" : k), "");
assert.equal(type("4500"), "4500");
assert.equal(type("0007"), "7");                    // no leading zeros
assert.equal(type(".5"), "0.5");                     // a leading dot becomes 0.
assert.equal(type("12.345"), "12.34");               // two decimals at most
assert.equal(type("1.2.3"), "1.23");                 // one dot only
assert.equal(type("123456789"), "12345678");         // eight whole digits at most
assert.equal(type("45<"), "4");
assert.equal(type("<<"), "");
assert.equal(rupees(4500000), "₹45,000");
assert.equal(rupees(450050), "₹4,500.50");
assert.equal(rupees(50000000), "₹5,00,000");          // Indian digit grouping
for (const s of ["AWAITING_VERIFICATION", "UNDER_REVIEW", "DONE", "DECLINED", "EXPIRED", "CANCELLED", "FAILED"]) assert.ok(STATUS[s]);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_amount_typing_and_formatting():
    r = subprocess.run(["node", "--input-type=module", "-e", CHECK % UI.as_uri()], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
