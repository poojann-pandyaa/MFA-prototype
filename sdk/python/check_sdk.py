"""Smoke check for the BUILT package, run in a clean environment with the wheel installed:

    python -I check_sdk.py

-I keeps this folder off the import path, so it tests the installed wheel and not the source file beside it.
"""
import hashlib
import hmac
import time

import amfa_client as a

assert "site-packages" in a.__file__, f"this tested the source tree, not the package: {a.__file__}"

h = a.sign_request("s3cret", "ak_test", "POST", "/v1/users?b=2&a=1", b"{}")
assert set(h) == {"X-AMFA-Key-Id", "X-AMFA-Timestamp", "X-AMFA-Nonce", "X-AMFA-Signature"} and h["X-AMFA-Key-Id"] == "ak_test"

ts, body = str(int(time.time())), b'{"x":1}'
sig = "v1=" + hmac.new(b"whsec", ts.encode() + b"." + body, hashlib.sha256).hexdigest()
assert a.verify_webhook("whsec", body, ts, sig)
assert not a.verify_webhook("whsec", body + b" ", ts, sig)               # a changed body is refused
assert not a.verify_webhook("whsec", body, str(int(ts) - 10_000), sig)   # an old timestamp is refused

b = a.proof_binding("tn", "c", "t", "e", 100, "INR", "p", "TRANSFER", 1)
assert b == a.proof_binding("tn", "c", "t", "e", 100, "INR", "p", "TRANSFER", 1)
assert b != a.proof_binding("tn", "c", "t", "e", 101, "INR", "p", "TRANSFER", 1)   # another amount, another binding

print("amfa-client OK", a.__file__)
