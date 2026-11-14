"""Give the demo bank customers with a past.

    python scripts/seed_demo.py --tenant demo-bank --bank-state data/demo-bank-state.json --home-ip 127.0.0.1 --home-ip 172.16.134.77

For each persona in amfa/demo/personas.py this writes into AMFA's database their payments, devices, usual networks,
payees and usual hours; teaches the behaviour service the same history (and a typing template, for personas who have
one); and adds their account, payees and activity to the bank's state file. Re-running replaces them. Stop the demo bank
first and start it again after, because it keeps its accounts in memory.

Every persona signs in with the password below. Development only.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AMFA_ENV", "development")

from argon2 import PasswordHasher  # noqa: E402
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from amfa.core import db, factors  # noqa: E402
from amfa.core.context_builder import device_key, payee_key  # noqa: E402
from amfa.demo import personas as P  # noqa: E402
from amfa.demo.synth import tempo_of, trace, typing_samples  # noqa: E402

PASSWORD = "DemoPass#2026"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="demo-bank", help="the bank's slug")
    ap.add_argument("--learning", action="store_true", help="turn on the learning phase: new customers use the phone's fingerprint or screen lock")
    ap.add_argument("--bank-state", help="the demo bank's state file (BANK_STATE_FILE)")
    ap.add_argument("--home-ip", action="append", default=None, help="an address the customers usually bank from (repeatable)")
    ap.add_argument("--only", action="append", help="seed only these usernames")
    ap.add_argument("--device", action="append", help="make these the customers' own phones (the app's Settings shows its device id); repeatable")
    a = ap.parse_args()
    homes = tuple(a.home_ip or ["127.0.0.1"])
    now = time.time()
    db.migrate()
    with db.session() as s:
        tenant = s.execute(select(db.Tenant).where(db.Tenant.slug == a.tenant)).scalars().one()
        # let customers choose their own no-extra-check limit; no waiting period here so a presenter can show it
        pol = dict(tenant.policy)
        pol["customerBypass"] = {"enabled": True, "maxMinor": 5_000_000, "coolingOffHours": 0}
        if a.learning:   # new sign-ups confirm with the phone's fingerprint or screen lock until the model knows them (needs app 1.0.3+)
            pol["learning"] = {"enabled": True}
            pol["permittedFactors"] = ["OTP", "DEVICE", "UEBA", "FACE", "VOICE"]
        tenant.policy = pol
        flag_modified(tenant, "policy")
    chosen = [p for p in P.PERSONAS if not a.only or p.username in a.only]
    if a.device:   # the demo phones become every customer's usual phone, so signing in on them is "their own phone"
        from dataclasses import replace
        chosen = [replace(p, devices=tuple(a.device) + p.devices[len(a.device):]) for p in chosen]

    state = {}
    if a.bank_state and Path(a.bank_state).exists():
        state = json.loads(Path(a.bank_state).read_text())
    ph = PasswordHasher()
    pw_hash = ph.hash(PASSWORD)

    ueba_up = factors.ueba("/risk/preview", {"tenantId": tenant.id, "userId": "cu_probe", "action": "TRANSFER", "amount": 1.0,
                                             "deviceKey": "probe", "locationKey": "net:probe"}) is not None
    phrase = factors.ueba_phrase() if ueba_up else None
    for p in chosen:
        with db.session() as s:
            cid = P.seed_amfa(s, tenant, p, now=now, home_ips=homes)
        learned = enrolled = 0
        if ueba_up:
            factors.erase("UEBA", tenant.id, cid)
            for i, t in enumerate(P.history(p, now)):
                net = P.geo.locate(homes[0]).network
                body = {"tenantId": tenant.id, "userId": cid, "action": "PAYMENT" if t.kind == "PAYMENT" else "TRANSFER", "amount": t.amount,
                        "payeeKey": payee_key(tenant.id, t.payee), "deviceKey": device_key(tenant.id, t.device),
                        "locationKey": f"net:{net}", "channel": "MOBILE",
                        "occurredAt": datetime.fromtimestamp(t.at, timezone.utc).isoformat()}
                learned += factors.ueba("/events/record", body) is not None
            if p.typing and phrase:
                rng = random.Random(p.username)
                tempo = tempo_of(p.username)
                payload = {"keystrokeSamples": typing_samples(rng, phrase["phrase"], phrase["repetitions"], tempo),
                           "shapeTraces": [trace(rng, phrase["shape"]) for _ in range(phrase["shapeTracesRequired"])],
                           "input": "touch", "shapePointerTypes": ["touch"] * phrase["shapeTracesRequired"]}
                enrolled = factors.enroll("UEBA", tenant=tenant.id, user=cid, payload=payload).enrolled
        state[p.username] = P.bank_account(p, cid, pw_hash, now=now)
        print(f"{p.username:8s} {p.name:18s} {len(state[p.username]['activity']):3d} payments  "
              f"behaviour events {learned:3d}  typing template {'yes' if enrolled else 'no ' if p.typing else '-'}  {p.story}")
    if a.bank_state:
        tmp = Path(a.bank_state).with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, a.bank_state)
        print(f"\nbank accounts written to {a.bank_state}; restart the demo bank to load them.")
    print(f"sign in as any of: {', '.join(p.username for p in chosen)}   password: {PASSWORD}")
    if not ueba_up:
        print("note: the behaviour service was not reachable, so only AMFA's own history was seeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
