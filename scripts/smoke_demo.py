"""Drive a running demo stack the way the apps do, and check the whole path: every seeded customer, then several brand-new customers who
answer the sign-up questions differently, enrol a phone key, learn, and pay. Exits non-zero if anything is wrong.

    BANK=http://127.0.0.1:18050 python scripts/smoke_demo.py        (start the stack first; the learning phase should be on: seed_demo.py --learning)
"""
import os
import sys
import time
import uuid

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from amfa.common import crypto

BANK = os.environ.get("BANK", "http://127.0.0.1:18050")


class Paced(httpx.Client):
    """AMFA limits requests per address (240 a minute for customers); the script paces itself under that, like a person would."""
    def request(self, *a, **k):
        time.sleep(0.4)
        return super().request(*a, **k)

FAILS: list[str] = []


def check(ok, what):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(what)
    return ok


class Phone:
    def __init__(self, user, device):
        self.c, self.user, self.device = Paced(base_url=BANK, timeout=60), user, device
        self.key = ec.generate_private_key(ec.SECP256R1())

    def login(self, password="DemoPass#2026"):
        r = self.c.post("/api/login", json={"username": self.user, "password": password})
        if r.status_code < 300 and "set-cookie" in r.headers:
            self.c.headers["cookie"] = r.headers["set-cookie"].split(";")[0]
        return r

    def me(self):
        return self.c.get("/api/me", params={"deviceId": self.device}).json()

    def jwk(self):
        n = self.key.public_key().public_numbers()
        b = lambda v: crypto.b64e(v.to_bytes(32, "big"))
        return {"kty": "EC", "crv": "P-256", "x": b(n.x), "y": b(n.y)}

    def enrol_key(self):
        link = self.c.post("/api/amfa/enrol", json={"factors": ["OTP"]}).json()["url"]
        base, tok = link.split("/hosted")[0], link.split("#token=")[1]
        a = Paced(base_url=base, headers={"authorization": f"Bearer {tok}"}, timeout=60)
        r = a.post("/c/v1/enrollment/device", json={"deviceId": self.device, "publicJwk": self.jwk()})
        a.post("/c/v1/enrollment/complete", json={})
        return r.status_code == 200

    def pay(self, payee, amount, wrong=False):
        s = self.c.post("/api/actions/transfer", json={"deviceId": self.device, "payeeRef": payee, "amountRupees": amount, "camera": True,
                                                       "microphone": True, "touch": True, "pointer": False})
        if s.status_code >= 300:
            return None, s.status_code, s.text[:120]
        act = s.json()["action"]
        link = s.json().get("challengeUrl")
        if link:
            ss = Paced(base_url=link.split("/hosted")[0], headers={"authorization": "Bearer " + link.split("#token=")[1]}, timeout=60)
            for _ in range(6):
                ch = ss.get("/c/v1/session").json().get("challenge")
                if ch is None:
                    break
                if ch["factor"] == "DEVICE":
                    msg = ch["params"]["message"].encode() + (b"!" if wrong else b"")
                    payload = {"signature": crypto.b64e(self.key.sign(msg, ec.ECDSA(hashes.SHA256())))}
                elif ch["factor"] == "OTP" and ch["params"].get("mode") == "totp":
                    break                                   # an authenticator-app customer: the secret lives in their app, not here
                elif ch["factor"] == "OTP":
                    payload = {"code": self.c.get("/api/demo/code").json()["code"]}
                else:
                    break                                   # typing, face, voice: the persona has none enrolled for this phone
                r = ss.post(f"/c/v1/session/factors/{ch['factor']}", json={"challengeId": ch["challengeId"], "payload": payload})
                if r.status_code != 200:
                    break
        done = self.c.post(f"/api/actions/{act['id']}/complete").json()["action"]
        return done, 200, act["checks"]


PERSONAS = [("asha", "android_asha_phone", "BEN-ASHA1"), ("rohan", "android_rohan_phone", "BEN-ROHAN1"), ("meera", "android_meera_phone", "BEN-MEERA1"),
            ("suresh", "android_suresh_phone", "BEN-SURESH1"), ("priya", "android_priya_phone", "BEN-PRIYA1"), ("vikram", "android_vikram_phone", "BEN-VIKRAM1"),
            ("kiran", "android_kiran_phone", "BEN-KIRAN1"), ("anil", "android_anil_phone", "BEN-ANIL1"), ("dev", "android_dev_phone", None)]

NEW = [  # (name, month band, typical band): different people answering differently, one skipping
    ("careful saver", 0, 0), ("everyday spender", 2, 1), ("business owner", 4, 4), ("skips the questions", None, None), ("understates", 0, 0)]


def main():
    print("\nSeeded customers")
    for user, dev, payee in PERSONAS:
        p = Phone(user, dev)
        check(p.login().status_code < 300, f"{user}: signs in")
        me = p.me()
        check("learning" in me and "profile" in me and me["balance"] > 0, f"{user}: /api/me has balance, learning and profile ({me['learning']['phase']})")
        if payee:
            d, status, checks = p.pay(payee, 500)
            check(d is not None and d["status"] in ("DONE", "AWAITING_VERIFICATION", "UNDER_REVIEW"), f"{user}: a small payment starts ({checks if d else status})")
            big = p.c.post("/api/actions/transfer", json={"deviceId": dev, "payeeRef": payee, "amountRupees": min(60_000, me["balance"] / 100 * .5), "camera": True, "microphone": True,
                                                         "touch": True, "pointer": False})
            check(big.status_code in (201, 400), f"{user}: a large payment is decided, not an error ({big.status_code})")

    print("\nNew customers")
    for i, (name, month, typical) in enumerate(NEW):
        user = f"smoke{uuid.uuid4().hex[:6]}"
        p = Phone(user, f"android_smoke{uuid.uuid4().hex[:10]}")
        r = p.c.post("/api/register", json={"username": user, "name": "Smoke Test", "password": "long-password-1", "phone": "+919812345678",
                                            "email": f"{user}@example.com", "deviceId": p.device})
        check(r.status_code == 201, f"{name}: opens an account")
        p.login("long-password-1")
        check(p.enrol_key(), f"{name}: hands AMFA the phone's key")
        pr = p.c.post("/api/profile", json={"monthBand": month, "typicalBand": typical})
        check(pr.status_code == 200 and pr.json()["declared"] == (month is not None or typical is not None), f"{name}: sign-up answers kept as given")
        m = p.me()
        check(m["learning"]["phase"] == "LEARNING" and m["learning"]["verifiedPayments"] == 0, f"{name}: starts learning")
        check("Fingerprint or screen lock" in m["protection"]["have"], f"{name}: the phone's key is listed among the ways to confirm")
        d, st, checks = p.pay("BEN-42", 1500)
        check(d is not None and d["status"] == "DONE" and checks == ["fingerprint or screen lock"], f"{name}: Rs1,500 needs only the phone's fingerprint or screen lock ({checks})")
        d, st, checks = p.pay("BEN-42", 1500, wrong=True)
        check(d is not None and d["status"] != "DONE", f"{name}: a wrong signature does not pay")
        d, st, checks = p.pay("BEN-42", 30_000)
        check(d is not None and d["status"] == "DONE" and set(checks) == {"fingerprint or screen lock", "one-time code"}, f"{name}: Rs30,000 adds a code ({checks})")
        after = p.me()["learning"]
        check(after["verifiedPayments"] == 2 and after["phase"] == "LEARNING", f"{name}: learned from its two verified payments ({after['verifiedPayments']})")
        if typical is not None:
            check(p.c.delete("/api/profile").json()["declared"] is False, f"{name}: takes the answers back")
    print(f"\n{'all checks passed' if not FAILS else str(len(FAILS)) + ' checks failed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
