"""Demo customers with a past, and the situations they are put in.

Each persona is one use case a bank cares about: an everyday small-payment user, a gig worker, a careful saver paying
rent, a senior who could be scammed, a merchant, a high-value customer, and a brand-new account. `seed` gives a
persona the history AMFA learns from (payments, devices, networks, payees, usual hours) and, for the demo bank, an
account with the same payees and activity. `SCENARIOS` are the situations the regression test and the demo walk
through: the same customer paying as usual, then paying in ways that should (or should not) cost more friction.

Dev and demo only: it writes straight into the database, as a data import would, instead of going through the API.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from amfa.common import crypto, geo
from amfa.core import db
from amfa.core.context_builder import device_key, payee_key
from amfa.engine.policy import TenantPolicy

DAY = 86_400.0
TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Payee:
    ref: str
    name: str
    display: str
    age_days: int                 # how long ago the customer saved it
    weight: float = 1.0           # how often it is paid compared with the others
    risk_flag: bool = False       # the bank's registry marks it as reported


@dataclass(frozen=True)
class Persona:
    username: str
    name: str
    story: str
    segment: str                  # STANDARD, SENIOR, PREMIUM, WATCHLIST
    account_age_days: int
    payees: tuple[Payee, ...]
    devices: tuple[str, ...]      # the phone first; a second one is used now and then
    per_week: float               # payments a week
    median: float                 # rupees
    sigma: float                  # spread of the amounts (log scale)
    hours: tuple[int, int]        # usual hours, local time, [start, end)
    history_days: int = 90
    recurring: tuple[tuple[str, float, int], ...] = ()       # (payee ref, rupees, day of the month)
    weekly: tuple[tuple[str, float, int], ...] = ()          # (payee ref, rupees, weekday 0=Monday): a big payment every week
    balance: float = 500_000.0
    typing: bool = False          # has a typing template (the demo can also enrol one live)
    authenticator: bool = False
    face: bool = False            # enrolled for a face check; only the regression gives these (a face can't be faked into the demo)
    voice: bool = False
    phone: str = "+919800000000"

    @property
    def email(self) -> str:
        return f"{self.username}@example.com"


def _p(ref, name, last4, age, weight=1.0, flag=False):
    return Payee(ref, name, f"{name} ••{last4}", age, weight, flag)


PERSONAS: tuple[Persona, ...] = (
    Persona("asha", "Asha Rao", "Everyday small payments: groceries, milk, the odd bill. Same phone, same hours.",
            "STANDARD", 900,
            (_p("BEN-ASHA1", "Fresh Mart", "2201", 700, 5), _p("BEN-ASHA2", "Dairy Ram", "7712", 600, 4),
             _p("BEN-ASHA3", "Vikram Mehta", "4411", 400, 1)),
            ("android_asha_phone",), 6, 250, 0.6, (8, 21), recurring=(("BILL-ELEC", 1800, 5),), balance=120_000, typing=True),
    Persona("rohan", "Rohan Das", "Gig worker: many small payments late into the evening, two phones.",
            "STANDARD", 500,
            (_p("BEN-ROHAN1", "Fuel Point", "1180", 400, 4), _p("BEN-ROHAN2", "Suresh Auto", "3302", 300, 2),
             _p("BEN-ROHAN3", "Cafe Nine", "9046", 200, 3)),
            ("android_rohan_phone", "android_rohan_old"), 14, 400, 0.8, (17, 24), balance=45_000, typing=True),
    Persona("meera", "Meera Iyer", "Salaried and careful: a few payments, and the rent to the same landlord every month.",
            "STANDARD", 1500,
            (_p("BEN-MEERA1", "Landlord K Nair", "5520", 800, 1), _p("BEN-MEERA2", "SIP Fund House", "6001", 700, 1),
             _p("BEN-MEERA3", "Priya Iyer", "7788", 900, 1)),
            ("android_meera_phone",), 1.5, 3000, 0.9, (9, 22),
            recurring=(("BEN-MEERA1", 25_000, 1), ("BEN-MEERA2", 5_000, 7)), balance=350_000, typing=True, authenticator=True, face=True),
    Persona("suresh", "Suresh Pillai", "Senior, little digital banking: a few small payments, and the kind of customer a "
            "scammer coaches into a big first payment.", "SENIOR", 4000,
            (_p("BEN-SURESH1", "Pharmacy Care", "3310", 900, 3), _p("BEN-SURESH2", "Anita Pillai", "8841", 1200, 1)),
            ("android_suresh_phone",), 0.8, 700, 0.5, (9, 17), balance=800_000),
    Persona("priya", "Priya Sharma", "Small business: many payees, many payments a day, larger amounts, in working hours.",
            "STANDARD", 1200,
            tuple(_p(f"BEN-PRIYA{i}", n, f"{4000 + i * 37}", 300 + i * 40, w) for i, (n, w) in enumerate(
                [("Metro Wholesale", 6), ("Packaging Co", 3), ("Courier Hub", 4), ("Print Works", 2), ("Raw Goods Ltd", 5)], 1)),
            ("android_priya_phone",), 35, 9000, 0.9, (9, 19), balance=1_500_000, typing=True, face=True),
    Persona("vikram", "Vikram Malhotra", "High-value customer: a few large transfers to the same people.",
            "PREMIUM", 2500,
            (_p("BEN-VIKRAM1", "Malhotra Family Trust", "1001", 1500, 2), _p("BEN-VIKRAM2", "Wealth Advisors", "2002", 1200, 1)),
            ("android_vikram_phone",), 1, 150_000, 0.6, (10, 19), balance=5_000_000, typing=True, authenticator=True, face=True, voice=True),
    Persona("kiran", "Kiran Shah", "Pays about a thousand or two almost every day to the same few people. A payment of ten thousand "
            "or a lakh would be a big event for Kiran.", "STANDARD", 700,
            (_p("BEN-KIRAN1", "Tiffin Centre", "5101", 500, 4), _p("BEN-KIRAN2", "Auto Stand", "5102", 450, 3), _p("BEN-KIRAN3", "Mother", "5103", 600, 2)),
            ("android_kiran_phone",), 7, 1500, 0.25, (17, 21), balance=300_000, typing=True, face=True),
    Persona("anil", "Anil Kapoor", "Runs a trading business: a few thousand a day to many small suppliers, and about a lakh "
            "every Monday to the same wholesaler. A lakh is a normal Monday for Anil; five lakh would not be.", "STANDARD", 1100,
            (_p("BEN-ANIL1", "Kapoor Wholesale", "6101", 800, 0), _p("BEN-ANIL2", "Corner Supplies", "6102", 700, 3), _p("BEN-ANIL3", "Gupta Traders", "6103", 650, 3)),
            ("android_anil_phone",), 8, 3000, 0.35, (10, 19), weekly=(("BEN-ANIL1", 100_000, 0),), balance=2_500_000, typing=True, face=True, voice=True),
    Persona("dev", "Dev Anand", "Brand-new customer: opened this week, no past to learn from.",
            "STANDARD", 5, (), ("android_dev_phone",), 0, 0, 0, (10, 20), history_days=5, balance=25_000),
)


def persona(username: str) -> Persona:
    return next(p for p in PERSONAS if p.username == username)


@dataclass
class Txn:
    at: float
    payee: str
    amount: float                 # rupees
    kind: str = "TRANSFER"        # TRANSFER or PAYMENT (a bill)
    device: str = ""


def history(p: Persona, now: float, seed: str = "") -> list[Txn]:
    """The persona's past payments, newest last. Deterministic for a persona, so a re-seed gives the same life."""
    rng = random.Random(f"{p.username}{seed}")
    out: list[Txn] = []
    weights = [x.weight for x in p.payees] or [1]
    weeks = p.history_days / 7
    for _ in range(int(p.per_week * weeks)):
        day = rng.uniform(0, p.history_days)
        hour = min(p.hours[1] - 1, max(p.hours[0], int(rng.gauss((p.hours[0] + p.hours[1]) / 2, (p.hours[1] - p.hours[0]) / 4))))
        at = now - day * DAY
        at = datetime.fromtimestamp(at, TZ).replace(hour=hour % 24, minute=rng.randint(0, 59), second=rng.randint(0, 59)).timestamp()
        payee = rng.choices(p.payees, weights)[0]
        if at < now - payee.age_days * DAY:
            continue
        amount = max(20.0, round(rng.lognormvariate(math.log(p.median), p.sigma), 0))
        out.append(Txn(at, payee.ref, amount, "TRANSFER", p.devices[0] if rng.random() < 0.9 else p.devices[-1]))
    local_now = datetime.fromtimestamp(now, TZ)
    for ref, amount, weekday in p.weekly:
        day = local_now.replace(hour=11, minute=5, second=0, microsecond=0)
        day -= timedelta(days=(day.weekday() - weekday) % 7)
        for w in range(int(p.history_days // 7) + 1):
            when = (day - timedelta(days=7 * w)).timestamp()
            if now - p.history_days * DAY <= when < now - 3600:
                out.append(Txn(when, ref, round(amount * rng.uniform(.85, 1.15), -2), "TRANSFER", p.devices[0]))
    for ref, amount, dom in p.recurring:
        for m in range(int(p.history_days // 30) + 2):
            year, month = local_now.year, local_now.month - m
            while month < 1:
                month, year = month + 12, year - 1
            when = datetime(year, month, min(dom, 28), 11, 5, tzinfo=TZ).timestamp()
            if now - p.history_days * DAY <= when < now - 3600:
                out.append(Txn(when, ref, amount, "PAYMENT" if ref.startswith("BILL-") else "TRANSFER", p.devices[0]))
    return sorted(out, key=lambda t: t.at)


def seed_amfa(s, tenant: db.Tenant, p: Persona, *, now: Optional[float] = None, home_ips: tuple[str, ...] = ("127.0.0.1",),
              biometrics: bool = False) -> str:
    """Create (or replace) the persona's customer in AMFA with their history. Returns the customer id."""
    now = now or time.time()
    cid = f"cu_demo_{p.username}"
    # everything that hangs off the customer, children first (sessions, decisions, an authenticator from an earlier run...)
    sessions = select(db.AuthSession.id).where(db.AuthSession.customer_id == cid)
    txns = select(db.Transaction.id).where(db.Transaction.customer_id == cid)
    for model in (db.Challenge, db.OtpCode, db.Attempt):          # what hangs off a session or a transaction has no customer id
        s.execute(delete(model).where(model.session_id.in_(sessions)))
    for model in (db.Proof, db.ReviewCase, db.RiskLabel):
        s.execute(delete(model).where(model.transaction_id.in_(txns)))
    for table in reversed(db.Base.metadata.sorted_tables):
        if table.name != "customer" and "customer_id" in table.c:
            s.execute(delete(table).where(table.c.customer_id == cid))
    s.execute(delete(db.Customer).where(db.Customer.id == cid))
    s.flush()

    contact = crypto.seal(json.dumps({"channel": "email", "to": p.email}).encode(), f"contact|{cid}", "contact")
    txns = history(p, now)
    hist = [0] * 24
    for t in txns:
        hist[datetime.fromtimestamp(t.at, TZ).hour] += 1
    s.add(db.Customer(id=cid, tenant_id=tenant.id, external_ref=f"CUST-{p.username}", segment=p.segment, contact_sealed=contact,
                      created_at=now - p.account_age_days * DAY, last_verified_at=(txns[-1].at if txns else now - DAY),
                      verified_count=len(txns), hour_histogram=hist))
    nets = {geo.locate(ip).network for ip in home_ips}
    first = txns[0].at if txns else now - p.account_age_days * DAY
    for i, dev in enumerate(p.devices):
        s.add(db.Device(customer_id=cid, device_key=device_key(tenant.id, dev), first_seen=first if i == 0 else now - 60 * DAY,
                        last_seen=now - DAY, verified_count=sum(1 for t in txns if t.device == dev) or 1))
    for n in nets:
        s.add(db.KnownNetwork(customer_id=cid, network=n, place_key=None, first_seen=first, last_seen=now - DAY, count=len(txns)))
    seen: dict[str, list[float]] = {}
    for i, t in enumerate(txns):
        pk = payee_key(tenant.id, t.payee)
        seen.setdefault(pk, []).append(t.at)
        net = sorted(nets)[0]
        s.add(db.Transaction(
            id=f"txn_demo_{p.username}_{i:04d}", tenant_id=tenant.id, customer_id=cid, external_id=f"demo-{p.username}-{i}",
            idempotency_key=f"demo-{p.username}-{i}", request_hash="demo", type=t.kind, amount_minor=int(t.amount * 100),
            currency="INR", payee_ref=t.payee, channel="MOBILE", status="AUTHORIZED",
            facts={"payeeKey": pk, "deviceKey": device_key(tenant.id, t.device), "network": net, "placeKey": None,
                   "locationKey": f"net:{net}"},
            created_at=t.at, updated_at=t.at))
    for pk, times in seen.items():
        s.add(db.KnownPayee(customer_id=cid, payee_key=pk, first_seen=min(times), count=len(times)))
    if p.typing:
        s.add(db.Enrollment(customer_id=cid, factor="UEBA", detail={"inputs": ["touch"], "shapeInputs": ["touch"], "shape": False}))
    if biometrics:   # plan-level only: there is no template behind these, so the live demo leaves them out
        for flag, factor in ((p.face, "FACE"), (p.voice, "VOICE")):
            if flag:
                s.add(db.Enrollment(customer_id=cid, factor=factor, detail={"model": "regression"}))
    if p.authenticator:
        s.add(db.TotpCredential(customer_id=cid, secret_sealed=crypto.seal(b"JBSWY3DPEHPK3PXP", f"totp|{cid}", "totp"), confirmed=True))
        s.add(db.Enrollment(customer_id=cid, factor="OTP", detail={"method": "totp"}))
    return cid


def bank_account(p: Persona, customer_id: str, password_hash: str, *, now: Optional[float] = None,
                 home_devices: tuple[str, ...] = ()) -> dict:
    """The same customer as the demo bank keeps them: account, payees, trusted devices and activity (newest first)."""
    now = now or time.time()
    txns = history(p, now)
    names = {x.ref: x for x in p.payees}
    billers = {"BILL-ELEC": "City Electricity", "BILL-MOBILE": "Mobile postpaid", "BILL-GAS": "Piped gas"}
    activity = []
    for i, t in enumerate(txns):
        display = names[t.payee].display if t.payee in names else billers.get(t.payee, t.payee)
        activity.append({"id": f"ACT{p.username.upper()}{i:05d}", "kind": "bill" if t.kind == "PAYMENT" else "transfer", "status": "DONE",
                         "createdAt": t.at, "title": ("Paid " if t.kind == "PAYMENT" else "Sent to ") + display,
                         "amountMinor": int(t.amount * 100), "payeeRef": t.payee, "payeeDisplay": display, "detail": None,
                         "reported": False, "params": {}, "checks": [], "confirmedWith": [], "reason": None,
                         "scamWarning": False, "reviewAfter": False})
    activity.reverse()
    sent_today = sum(a["amountMinor"] for a in activity if datetime.fromtimestamp(a["createdAt"], TZ).date() == datetime.fromtimestamp(now, TZ).date())
    return {"ref": f"CUST-{p.username}", "name": p.name, "password": password_hash, "phone": p.phone, "email": p.email,
            "balance": int(p.balance * 100), "amfaUser": customer_id,
            "dailyLimit": 200_000_00 if p.segment != "PREMIUM" else 1_000_000_00, "sent": {"day": datetime.fromtimestamp(now, TZ).date().isoformat(), "minor": sent_today},
            "payees": {x.ref: {"name": x.name, "display": x.display, "createdAt": now - x.age_days * DAY,
                               **({"riskFlag": True} if x.risk_flag else {})} for x in p.payees},
            "devices": list(p.devices) + list(home_devices), "events": {}, "activity": activity, "createdAt": now - p.account_age_days * DAY}


# ---------------------------------------------------------------- situations

@dataclass(frozen=True)
class Scenario:
    persona: str
    name: str
    amount: float                         # rupees
    payee: str = "usual"                  # a payee ref the customer already pays, "usual" for their most-paid one, or "new"
    device: str = "usual"                 # "usual" or "new"
    hour: Optional[int] = None            # local hour; None keeps the customer's usual
    events: dict = field(default_factory=dict)      # accountEvents: simChangedAt / contactChangedAt / passwordResetAt (seconds ago)
    kind: str = "TRANSFER"
    network: str = "home"                 # "home" or "other"
    fraud: bool = False                   # the situation a careful bank would stop or challenge hard
    note: str = ""
