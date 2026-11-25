# Ported unchanged from branch Research/face-auth @ 2b057c5, backend/ato/simulate.py (DemoPay's UPI scam model).
# Only the import lines differ. See amfa/engine/risk/upi/__init__.py.
"""
Synthetic UPI payment streams: legitimate users (with hard look-alike behaviour) and 13 scam types
in three families (takeover, coached, payee-side).

THIS IS NOT REAL DATA. No labelled Indian UPI fraud data is public. Every signature here is an
assumption written from the public reporting summarised in docs/upi-fraud-scenarios-deep-dive.md and
docs/scam-coverage-and-data-needs.md. Both the scams and the detector are ours, so results partly
confirm our own assumptions; "stealth", signal dropout and unreported fraud are built in to make it harder.
What it is good for: exercising the full pipeline, stress-testing behaviour under missing signals and
adapting attackers, and finding which signals matter. What it cannot tell you: real-world precision/recall.
Replace `build_dataset` with a loader over labelled history from a bank/PSP; everything downstream is unchanged.

Fraud labels are produced as in shadow mode: nothing is blocked while generating, so a whole incident's
payments are observable, like logged data before an enforcement system exists.
"""
import datetime
import math
from collections import namedtuple
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .features import DAY, FEATURES, IST_SECONDS, Event, UserState, featurize

T0 = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc).timestamp()  # day 0 (midnight UTC)
PRE_DAYS = 30        # unlabelled warm-up before day 0 so most users have history
TOTAL_DAYS = 150     # labelled timeline: days 0..150

# Assumption knobs. The defaults are what the shipped model was trained on; ato/evaluate.py overrides them to
# test how the trained model behaves when the real world differs from those assumptions.
SIM = dict(fri_cover=1.0,         # how well government fraud-risk data covers scam payees (1 = as trained)
           attacker_sdk_p=0.6,    # chance a takeover attacker's own phone reports telemetry
           legit_call_rate=0.04,  # share of genuine payments made while on a call
           sdk_ok_p=0.95,         # chance the SDK reports on a genuine payment
           phone_change_rate=0.15,
           night_owl_share=0.12,
           rate_median=1.2,       # payments per day for the median user
           amount_scale=1.0,      # scales every user's typical payment
           new_payee_scale=1.0,   # scales the share of genuine payments going to new payees
           stealth_shift=0.0,     # added to every scammer's stealth
           unreported=0.15,       # share of scam incidents never reported (labelled legitimate)
           mule_aged_boost=0.0,   # added to the chance a scam payee is an aged (rented) account instead of a young one
           mix=None)              # override the scam-type mix


class override:
    """with override(fri_cover=0.3): ...   (temporarily changes simulator assumptions)"""
    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            assert k in SIM, k
            self.old[k] = SIM[k]; SIM[k] = v

    def __exit__(self, *a):
        SIM.update(self.old)


TAKEOVER = ["sim_swap", "apk_binding", "remote_access", "physical_theft"]
COACHED = ["digital_arrest", "fake_care", "collect_qr", "task_invest", "extortion", "prize_fee", "relative_trouble", "romance"]
PAYEE_SIDE = ["lookalike", "fake_seller"]
ALL_TYPES = TAKEOVER + COACHED + PAYEE_SIDE
FAMILY = {**{t: "takeover" for t in TAKEOVER}, **{t: "coached" for t in COACHED}, **{t: "payee" for t in PAYEE_SIDE}}
MIX = {"sim_swap": .07, "apk_binding": .07, "remote_access": .07, "physical_theft": .07,
       "digital_arrest": .10, "fake_care": .10, "collect_qr": .12, "task_invest": .10, "extortion": .06,
       "prize_fee": .06, "relative_trouble": .05, "romance": .04, "lookalike": .08, "fake_seller": .08}
TELEMETRY = ["bio_similarity", "remote_access_active", "integrity_fail", "overlay_accessibility",
             "ip_geo_km", "login_to_pay_s", "failed_pin_attempts"]  # a takeover attacker can try to make these look normal

# ---- payee identities ---------------------------------------------------------------------------
Payee = namedtuple("Payee", "vpa born fan merchant risky fri complaints")
SUFFIX = ["okaxis", "oksbi", "ybl", "paytm", "okhdfcbank", "upi", "ibl"]
SYL = ["ra", "me", "sh", "ku", "ma", "ni", "pa", "ti", "an", "sa", "vi", "jay", "dee", "lak", "mo", "ha", "re", "su", "ka", "bhu", "pri", "ya"]


def _pick(rng, seq):
    return seq[int(rng.integers(len(seq)))]


def new_vpa(rng) -> str:
    h = "".join(_pick(rng, SYL) for _ in range(int(rng.integers(2, 5))))
    if rng.random() < 0.4:
        h += str(int(rng.integers(1, 99)))
    return f"{h}@{_pick(rng, SUFFIX)}"


def mutate_vpa(v: str, rng) -> str:
    h, s = v.split("@")
    k = int(rng.integers(5))
    if k == 0 and len(h) > 3:
        i = int(rng.integers(len(h) - 1)); h = h[:i] + h[i + 1] + h[i] + h[i + 2:]
    elif k == 1:
        i = int(rng.integers(len(h))); h = h[:i] + _pick(rng, "abcdefghijklmnopqrstuvwxyz") + h[i + 1:]
    elif k == 2:
        h += _pick(rng, "0123456789")
    elif k == 3:
        s = _pick(rng, [x for x in SUFFIX if x != s])
    else:
        i = int(rng.integers(len(h))); h = h[:i] + h[i] + h[i:]
    out = f"{h}@{s}"
    return out if out != v else out + "1"


def _legit_fri(rng):
    if rng.random() < 0.10:
        return None  # feed unavailable
    r = rng.random()
    return 0 if r < 0.972 else 1 if r < 0.992 else 2 if r < 0.998 else 3


def _legit_complaints(rng):
    return 1 if rng.random() < 0.003 else 0


def _fri_probs(p0, cover):
    if cover == 1.0:
        return p0
    none = min(1.0, max(0.0, 1 - (1 - p0[0]) * cover))
    scale = (1 - none) / (1 - p0[0])
    return [none] + [x * scale for x in p0[1:]]


def make_mule(rng, now: float, stealth: float, risky_p=0.0, merch_p=0.0, aged_p=None, vpa=None) -> Payee:
    """A fraud-controlled receiving account. Stealthier operators lean on aged (rented) accounts."""
    aged = rng.random() < min(1.0, (aged_p if aged_p is not None else 0.2 + 0.5 * stealth) + SIM["mule_aged_boost"])
    if aged:
        born, fan = now - float(rng.uniform(100, 900)) * DAY, int(rng.integers(0, 10))
        p = _fri_probs([.6, .2, .15, .05], SIM["fri_cover"]); compl = int(rng.poisson(0.3))
    else:
        born, fan = now - float(rng.uniform(0, 12)) * DAY, int(rng.integers(3, 40))
        p = _fri_probs([.25, .25, .30, .20], SIM["fri_cover"]); compl = int(rng.poisson(1.2))
    fri = None if rng.random() < 0.15 else int(rng.choice(4, p=p))
    return Payee(vpa or new_vpa(rng), born, fan, bool(rng.random() < merch_p), bool(rng.random() < risky_p), fri, compl)


def _pf(p: Payee, ts: float) -> Dict:
    return dict(payee=p.vpa, payee_age_days=(ts - p.born) / DAY, payee_fan_in_24h=p.fan, payee_is_merchant=p.merchant,
                payee_risky_category=p.risky, payee_fri=p.fri, payee_complaints=p.complaints)


# ---- users ---------------------------------------------------------------------------------------
@dataclass
class User:
    uid: int
    median: float
    sigma: float
    base_bal: float
    rate: float
    hour_p: np.ndarray
    join_day: float
    regulars: list           # Payee
    reg_w: np.ndarray
    bio_mean: float
    phone_ts: Optional[float]
    sim_ts: Optional[float]
    keep_old: bool
    pin_reset_after_change: bool
    pin_offset_h: float
    trips: list              # (start_ts, end_ts, km)
    sideloads: list          # ts
    rooted: bool
    accessibility: bool
    vpn: float
    dev0: str
    dev1: str
    kind: str = "day"


def _hour_dist(kind):
    c, sd = {"day": (14, 3.5), "night": (22, 3.0), "early": (9, 2.5)}[kind]
    h = np.arange(24)
    d = np.minimum(abs(h - c), 24 - abs(h - c))
    p = np.exp(-0.5 * (d / sd) ** 2) + 0.004
    return p / p.sum()


def make_user(rng, uid: int) -> User:
    kind = rng.choice(["day", "night", "early"], p=[0.92 - SIM["night_owl_share"], SIM["night_owl_share"], 0.08])
    n_reg = int(rng.integers(4, 15))
    w = 1.0 / np.arange(1, n_reg + 1)
    join = -PRE_DAYS if rng.random() < 0.85 else float(rng.uniform(-PRE_DAYS, 120))
    phone = float(rng.uniform(max(join, 0) + 10, TOTAL_DAYS)) * DAY + T0 if rng.random() < SIM["phone_change_rate"] else None
    sim = float(rng.uniform(max(join, 0) + 10, TOTAL_DAYS)) * DAY + T0 if rng.random() < 0.06 else None
    if phone and sim and rng.random() < 0.2:
        sim = phone
    trips = []
    for _ in range(2):
        if rng.random() < 0.3:
            s = T0 + float(rng.uniform(0, TOTAL_DAYS - 10)) * DAY
            trips.append((s, s + float(rng.uniform(3, 10)) * DAY, float(rng.uniform(150, 1500))))
    regulars = []
    for _ in range(n_reg):
        merchant = bool(rng.random() < 0.3)
        regulars.append(Payee(new_vpa(rng), T0 - float(rng.uniform(30, 3000)) * DAY, int(rng.integers(5, 200)) if merchant else int(rng.integers(0, 6)),
                              merchant, bool(merchant and rng.random() < 0.02), _legit_fri(rng), _legit_complaints(rng)))
    return User(
        uid=uid, median=math.exp(rng.uniform(math.log(80), math.log(2000))) * SIM["amount_scale"], sigma=float(rng.uniform(0.6, 1.0)),
        base_bal=0.0, rate=math.exp(rng.normal(math.log(SIM["rate_median"]), 0.7)), hour_p=_hour_dist(kind), join_day=join,
        regulars=regulars, reg_w=w / w.sum(), bio_mean=float(np.clip(rng.normal(0.85, 0.05), 0.6, 0.95)),
        phone_ts=phone, sim_ts=sim, keep_old=bool(rng.random() < 0.25), pin_reset_after_change=bool(rng.random() < 0.5),
        pin_offset_h=float(rng.uniform(0, 0.5)), trips=trips,
        sideloads=[T0 + float(rng.uniform(0, TOTAL_DAYS)) * DAY for _ in range(int(rng.integers(1, 4)))] if rng.random() < 0.15 else [],
        rooted=bool(rng.random() < 0.015), accessibility=bool(rng.random() < 0.01),
        vpn=0.5 if rng.random() < 0.03 else 0.0, dev0=f"d{uid}_0", dev1=f"d{uid}_1", kind=str(kind),
    )


def _device_at(u: User, ts: float) -> str:
    return u.dev1 if u.phone_ts and ts >= u.phone_ts else u.dev0


def _legit_telemetry(u: User, ts: float, rng) -> Dict:
    """Signals for a payment made by the genuine user, including look-alike episodes."""
    sig: Dict = {}
    tags = []
    sdk = rng.random() <= SIM["sdk_ok_p"]  # SDK reports this time
    if u.phone_ts and 0 <= ts - u.phone_ts < 72 * 3600:
        hrs = (ts - u.phone_ts) / 3600
        sig["device_bound_hours"] = hrs
        sig["old_device_active"] = u.keep_old
        if u.pin_reset_after_change:
            sig["pin_reset_hours"] = max(0.0, hrs - u.pin_offset_h)
        tags.append("phone_change")
    if u.sim_ts and 0 <= ts - u.sim_ts < 96 * 3600:
        sig["sim_changed_hours"] = (ts - u.sim_ts) / 3600
        tags.append("sim_replace")
    geo = abs(rng.normal(0, 8)) if rng.random() > 0.10 else None
    for s, e, km in u.trips:
        if s <= ts <= e:
            geo = km + rng.normal(0, 20)
            tags.append("travel")
    if rng.random() < u.vpn:
        geo = float(rng.uniform(300, 5000))
        tags.append("vpn")
    sig["ip_geo_km"] = geo
    f = rng.random()
    sig["failed_pin_attempts"] = 0 if f < 0.96 else 1 if f < 0.995 else 2
    if sdk:
        sig["remote_access_active"] = bool(rng.random() < 0.0025)
        if sig["remote_access_active"]:
            tags.append("screen_share")
        if rng.random() < 0.04:
            sig["bio_similarity"] = float(rng.uniform(0.2, 0.6))  # family member / odd grip
            tags.append("other_user")
        elif rng.random() > 0.05:
            sig["bio_similarity"] = float(np.clip(rng.normal(u.bio_mean, 0.07), 0, 1))
        sig["integrity_fail"] = u.rooted
        sig["overlay_accessibility"] = u.accessibility
        recent_sl = [ts - s_ for s_ in u.sideloads if 0 <= ts - s_ < 96 * 3600]
        if recent_sl:
            sig["sideload_hours"] = min(recent_sl) / 3600
            tags.append("sideload")
        sig["login_to_pay_s"] = float(math.exp(rng.normal(math.log(30), 0.6)))
        on_call = rng.random() < SIM["legit_call_rate"]  # people do talk while paying
        sig["call_active"] = bool(on_call)
        sig["call_minutes"] = float(rng.exponential(5)) if on_call else 0.0
        sig["call_unknown_number"] = bool(on_call and rng.random() < 0.15)
        if on_call:
            tags.append("call_during_payment")
    return {"sig": sig, "tags": tags}


def _balance(u, rng):
    return None if rng.random() < 0.05 else u.base_bal * math.exp(rng.normal(0, 0.4))


def gen_legit(u: User, rng, merchants) -> List:
    out = []
    start = max(u.join_day, -PRE_DAYS)
    for _ in range(rng.poisson(u.rate * (TOTAL_DAYS - start))):
        d = math.floor(rng.uniform(start, TOTAL_DAYS))
        h = int(rng.choice(24, p=u.hour_p))
        ts = T0 + d * DAY + h * 3600 + float(rng.uniform(0, 3600)) - IST_SECONDS
        r, tags, regular, mult = rng.random(), [], False, math.exp(rng.normal(0, u.sigma))
        ns = SIM["new_payee_scale"]
        t1 = 1 - 0.22 * ns; t2 = t1 + 0.14 * ns; t3 = t2 + 0.05 * ns
        if r < t1:
            p, regular = u.regulars[int(rng.choice(len(u.regulars), p=u.reg_w))], True
        elif r < t2:
            p, tags, mult = merchants[int(rng.integers(len(merchants)))], ["new_payee"], mult * rng.uniform(1, 2.5)
        elif r < t3:
            similar = rng.random() < 0.2  # e.g. a relative whose handle resembles one already saved
            vpa = mutate_vpa(_pick(rng, u.regulars).vpa, rng) if similar else new_vpa(rng)
            p = Payee(vpa, ts - float(rng.uniform(20, 3000)) * DAY, int(rng.integers(0, 5)), False, False, _legit_fri(rng), _legit_complaints(rng))
            tags, mult = ["new_payee"] + (["similar_legit_payee"] if similar else []), mult * rng.uniform(1, 2.5)
        else:
            p = Payee(new_vpa(rng), ts - float(rng.uniform(1, 30)) * DAY, int(rng.integers(0, 15)), bool(rng.random() < 0.5), False,
                      _legit_fri(rng), _legit_complaints(rng))
            tags, mult = ["new_young_payee"], mult * rng.uniform(1, 2.5)
        if rng.random() < 0.015:
            mult, tags = rng.uniform(8, 40), tags + ["big_oneoff"]
        c = rng.random()
        if p.merchant:
            channel = "qr" if c < 0.55 else "collect" if c < 0.59 else "pay"
        else:
            channel = "collect" if c < 0.06 else "qr" if c < 0.14 else "pay"
        contact = None
        if channel == "collect":
            contact = (regular or rng.random() < 0.3) if not p.merchant else bool(rng.random() < 0.3)
            if not contact and not regular:
                tags.append("collect_from_noncontact")
        bal = _balance(u, rng)
        t = _legit_telemetry(u, ts, rng)
        sig = t["sig"]
        if "call_active" in sig and channel == "collect":
            pass
        out.append((ts, Event(ts=ts, amount=min(u.median * mult, (bal or 1e9) * 0.6), device_id=_device_at(u, ts), balance=bal,
                              channel=channel, requester_is_contact=contact if "call_active" in sig else None, **_pf(p, ts), **sig),
                    (0, -1, 0.0, tags + t["tags"], "legit")))
    return out


# ---- takeover attacks (someone else is paying) --------------------------------------------------
def gen_takeover(u: User, rng, atype: str, start: float, stealth: float, inc: int) -> List:
    bal = u.base_bal * math.exp(rng.normal(0, 0.4))
    loud = stealth < 0.5
    k = int(rng.integers(1, 9)) if loud else int(rng.integers(3, 9))
    if loud:
        shares = rng.dirichlet(np.ones(k)) * bal * rng.uniform(0.3, 0.95)
        amounts = [min(float(s), 100000.0) for s in shares]
        if rng.random() < 0.4:  # a tiny probe payment first
            amounts = [float(rng.uniform(1, 10))] + amounts
    else:
        amounts = [min(u.median * float(rng.uniform(0.8, 3)), bal * 0.5) for _ in range(k)]
    ad = f"atk{inc}"
    b0, s0, sl0 = float(rng.uniform(0.1, 6)), float(rng.uniform(0.2, 40)), float(rng.uniform(1, 60))
    d0 = math.floor((start - T0) / DAY)
    h0 = (float(rng.uniform(0, 5)) if rng.random() < 0.45 else float(rng.uniform(0, 24))) if loud else int(rng.choice(24, p=u.hour_p)) + float(rng.uniform(0, 1))
    start = T0 + d0 * DAY + h0 * 3600 - IST_SECONDS
    ts, events, mules = start, [], []
    att_sdk = rng.random() < SIM["attacker_sdk_p"]  # does the attacker's own device run the telemetry SDK?
    for j, amt in enumerate(amounts):
        if j:
            ts += float(rng.exponential(240 if loud else 14 * 3600))
        el = (ts - start) / 3600
        base = _legit_telemetry(u, ts, rng)["sig"]
        for key in ("device_bound_hours", "old_device_active", "pin_reset_hours", "sim_changed_hours", "sideload_hours",
                    "call_active", "call_minutes", "call_unknown_number"):
            base.pop(key, None)
        b = lambda m, nan_p: None if rng.random() < 0.1 else float(np.clip(rng.normal(m, 0.12), 0, 1))  # noqa: E731
        # an attacker's own device either reports all its telemetry (like a normal client) or none of it; never a
        # tell-tale partial pattern the model could use as a shortcut
        no_call = dict(call_active=False, call_minutes=0.0, call_unknown_number=False)
        geo_far = None if rng.random() < 0.1 else (float(rng.uniform(150, 2500)) if rng.random() < 0.6 else abs(float(rng.normal(0, 15))))
        pin_new = (max(0.0, b0 - float(rng.uniform(0, 0.5))) + el) if rng.random() < 0.9 else None
        if atype == "sim_swap":
            dev, a = ad, dict(device_bound_hours=b0 + el, old_device_active=bool(rng.random() < 0.1), sim_changed_hours=s0 + el,
                              pin_reset_hours=pin_new, remote_access_active=False, bio_similarity=b(0.3, 0.4),
                              integrity_fail=bool(rng.random() < 0.35), overlay_accessibility=bool(rng.random() < 0.1),
                              ip_geo_km=geo_far, login_to_pay_s=float(rng.uniform(4, 20)), failed_pin_attempts=int(rng.random() > 0.85), **no_call)
        elif atype == "apk_binding":
            dev, a = ad, dict(device_bound_hours=b0 + el, old_device_active=bool(rng.random() < 0.8), pin_reset_hours=pin_new,
                              sideload_hours=(sl0 + el) if rng.random() < 0.85 else None, remote_access_active=False,
                              bio_similarity=b(0.3, 0.4), integrity_fail=bool(rng.random() < 0.3),
                              overlay_accessibility=bool(rng.random() < 0.3), ip_geo_km=geo_far,
                              login_to_pay_s=float(rng.uniform(4, 20)), failed_pin_attempts=int(rng.random() > 0.85), **no_call)
        elif atype == "remote_access":
            on_call = rng.random() < 0.3  # victim was being guided on a call to install the app
            dev, a = _device_at(u, ts), dict(remote_access_active=bool(rng.random() < 0.85), bio_similarity=b(0.25, 0.3),
                                             integrity_fail=bool(rng.random() < 0.05), overlay_accessibility=bool(rng.random() < 0.6),
                                             ip_geo_km=abs(float(rng.normal(0, 8))), login_to_pay_s=float(rng.uniform(2, 10)),
                                             failed_pin_attempts=int(rng.random() > 0.8), call_active=on_call,
                                             call_minutes=float(rng.uniform(3, 30)) if on_call else 0.0, call_unknown_number=on_call)
        else:  # physical_theft
            dev, a = _device_at(u, ts), dict(remote_access_active=False, bio_similarity=b(0.4, 0.15), integrity_fail=False,
                                             overlay_accessibility=False,
                                             ip_geo_km=float(rng.uniform(20, 500)) if rng.random() < 0.4 else abs(float(rng.normal(0, 8))),
                                             login_to_pay_s=float(rng.uniform(5, 25)),
                                             failed_pin_attempts=int(rng.choice([0, 1, 2, 3], p=[0.6, 0.2, 0.1, 0.1])),
                                             call_active=False, call_minutes=0.0, call_unknown_number=False)
        if atype in ("sim_swap", "apk_binding") and not att_sdk:
            for key in ("remote_access_active", "bio_similarity", "integrity_fail", "overlay_accessibility", "login_to_pay_s",
                        "call_active", "call_minutes", "call_unknown_number", "sideload_hours"):
                a[key] = None
        for key in TELEMETRY:  # stealthy attackers sometimes look normal on telemetry they can influence
            if key in a and rng.random() < 0.7 * stealth and key in base:
                a[key] = base[key]
            elif key in a and rng.random() < 0.7 * stealth:
                a[key] = None
        if j == 0 or not mules or rng.random() < 0.7:
            mules.append(make_mule(rng, ts, stealth))
        p = mules[-1] if (j == 0 or rng.random() < 0.7) else mules[int(rng.integers(len(mules)))]
        events.append((ts, Event(ts=ts, amount=float(amt), device_id=dev, balance=max(bal, 1.0), channel="pay", **_pf(p, ts), **a),
                       (1, inc, stealth, [atype], atype)))
        bal = max(bal - amt, 1.0)
    return events


# ---- coached scams (the real owner is paying) -----------------------------------------------------
SPEC = {  # aged = base chance the receiving account is an aged (rented) one; stealth pushes it higher
    "digital_arrest":   dict(n=(1, 4), gap_h=1.5, call=.95, cmin=(30, 300), unk=.7, ch=(1, 0, 0), remote=.10, aged=.4, risky=0, merch=0, night=.1, pool=(1, 2)),
    "fake_care":        dict(n=(1, 3), gap_h=.3, call=.85, cmin=(5, 60), unk=.8, ch=(.6, .4, 0), remote=.40, aged=.3, risky=0, merch=0, night=.05, pool=(1, 1)),
    "collect_qr":       dict(n=(1, 2), gap_h=.2, call=.25, cmin=(2, 15), unk=.6, ch=(0, .6, .4), remote=0, aged=.3, risky=0, merch=0, night=.05, pool=(1, 1)),
    "task_invest":      dict(n=(3, 8), gap_h=20, call=.05, cmin=(2, 10), unk=.5, ch=(.8, 0, .2), remote=0, aged=.3, risky=.4, merch=.3, night=.1, pool=(1, 3)),
    "extortion":        dict(n=(3, 6), gap_h=3, call=.10, cmin=(2, 10), unk=.5, ch=(1, 0, 0), remote=0, aged=.3, risky=0, merch=0, night=.4, pool=(1, 2)),
    "prize_fee":        dict(n=(1, 2), gap_h=1, call=.30, cmin=(2, 15), unk=.7, ch=(1, 0, 0), remote=0, aged=.15, risky=0, merch=.2, night=.05, pool=(1, 1)),
    "relative_trouble": dict(n=(1, 2), gap_h=2, call=.10, cmin=(2, 10), unk=.5, ch=(1, 0, 0), remote=0, aged=.9, risky=0, merch=0, night=.05, pool=(1, 1)),
    "romance":          dict(n=(4, 10), gap_h=120, call=.05, cmin=(2, 10), unk=.5, ch=(1, 0, 0), remote=0, aged=.9, risky=0, merch=0, night=.05, pool=(1, 1)),
    "fake_seller":      dict(n=(1, 2), gap_h=6, call=.03, cmin=(2, 10), unk=.5, ch=(.4, 0, .6), remote=0, aged=.3, risky=.1, merch=.6, night=.05, pool=(1, 2)),
    "lookalike":        dict(n=(1, 1), gap_h=1, call=.05, cmin=(2, 10), unk=.5, ch=(.5, 0, .5), remote=0, aged=0, risky=0, merch=.3, night=.05, pool=(1, 1)),
}


def _coached_amount(ftype, u, bal, j, rng):
    m = u.median
    return {"digital_arrest": lambda: bal * rng.uniform(0.25, 0.6), "fake_care": lambda: m * rng.uniform(1, 6),
            "collect_qr": lambda: m * rng.uniform(.5, 4), "task_invest": lambda: m * rng.uniform(1, 3) * (1 + 0.6 * j),
            "extortion": lambda: m * rng.uniform(.5, 2), "prize_fee": lambda: m * rng.uniform(.3, 2),
            "relative_trouble": lambda: m * rng.uniform(1, 8), "romance": lambda: m * rng.uniform(1, 3) * (1 + 0.1 * j),
            "lookalike": lambda: m * rng.uniform(.5, 2), "fake_seller": lambda: m * rng.uniform(1, 5)}[ftype]()


def gen_coached(u: User, rng, ftype: str, start: float, stealth: float, inc: int) -> List:
    sp = SPEC[ftype]
    n = int(rng.integers(sp["n"][0], sp["n"][1] + 1))
    bal = u.base_bal * math.exp(rng.normal(0, 0.4))
    d0 = math.floor((start - T0) / DAY)
    h0 = float(rng.uniform(0, 5)) if rng.random() < sp["night"] else int(rng.choice(24, p=u.hour_p)) + float(rng.uniform(0, 1))
    ts = T0 + d0 * DAY + h0 * 3600 - IST_SECONDS
    pool = []
    for _ in range(int(rng.integers(sp["pool"][0], sp["pool"][1] + 1))):
        look = mutate_vpa(_pick(rng, u.regulars).vpa, rng) if ftype == "lookalike" else None
        pool.append(make_mule(rng, ts, stealth, risky_p=sp["risky"], merch_p=sp["merch"],
                               aged_p=None if ftype == "lookalike" else sp["aged"] + 0.5 * stealth * (1 - sp["aged"]), vpa=look))
    events = []
    for j in range(n):
        if j:
            ts += float(rng.exponential(sp["gap_h"] * 3600))
        amt = _coached_amount(ftype, u, bal, j, rng)
        amt = (1 - 0.8 * stealth) * amt + 0.8 * stealth * u.median * float(rng.uniform(1, 3))  # careful scammers stay near the victim's norm
        amt = max(1.0, min(amt, bal * 0.9))
        base = _legit_telemetry(u, ts, rng)["sig"]
        sdk = "remote_access_active" in base
        hide = rng.random() < 0.7 * stealth          # scammer avoids tripping call/screen-share signals
        on_call = (rng.random() < sp["call"]) and not hide
        sig = dict(base)
        if sdk:
            sig["call_active"] = bool(on_call)
            sig["call_minutes"] = float(rng.uniform(*sp["cmin"])) if on_call else 0.0
            sig["call_unknown_number"] = bool(on_call and rng.random() < sp["unk"])
            if rng.random() < sp["remote"] and not hide:
                sig["remote_access_active"] = True
        c = rng.random()
        channel = "pay" if c < sp["ch"][0] else "collect" if c < sp["ch"][0] + sp["ch"][1] else "qr"
        p = pool[int(rng.integers(len(pool)))] if len(pool) < n else pool[j % len(pool)]
        events.append((ts, Event(ts=ts, amount=float(amt), device_id=_device_at(u, ts), balance=max(bal, 1.0), channel=channel,
                                 requester_is_contact=False if (sdk and channel == "collect") else None, **_pf(p, ts), **sig),
                       (1, inc, stealth, [ftype], ftype)))
        bal = max(bal - amt, 1.0)
    return events


def build_dataset(n_users: int = 6000, seed: int = 0, attacked_frac: float = 0.25) -> Dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    merchants = [Payee(new_vpa(rng), T0 - float(rng.uniform(60, 2500)) * DAY, int(rng.integers(5, 200)), True,
                       bool(rng.random() < 0.02), _legit_fri(rng), _legit_complaints(rng)) for _ in range(2000)]
    X, meta = [], {k: [] for k in ("day", "user", "y", "incident", "stealth", "amount", "tag", "atype", "user_kind", "user_rate", "user_median")}
    mix = SIM["mix"] or MIX
    types, weights = list(mix), np.array(list(mix.values()))
    inc, unreported = 0, {}
    order = ["phone_change", "sim_replace", "screen_share", "big_oneoff", "other_user", "collect_from_noncontact", "similar_legit_payee",
             "call_during_payment", "travel", "vpn", "sideload", "new_young_payee", "new_payee"]
    for uid in range(n_users):
        u = make_user(rng, uid)
        u.base_bal = u.median * float(rng.uniform(25, 300))
        evs = gen_legit(u, rng, merchants)
        if rng.random() < attacked_frac:
            ftype = types[int(rng.choice(len(types), p=weights / weights.sum()))]
            r = rng.random()
            stealth = float(rng.uniform(0, 0.3) if r < 0.5 else rng.uniform(0.3, 0.6) if r < 0.8 else rng.uniform(0.65, 1.0))
            start_day = float(rng.uniform(max(u.join_day + 8, 0.0), TOTAL_DAYS - 3))
            if start_day > 110:  # tactics drift: attackers get stealthier late in the timeline
                stealth = min(1.0, stealth + 0.15)
            stealth = min(1.0, stealth + SIM["stealth_shift"])
            gen = gen_takeover if ftype in TAKEOVER else gen_coached
            evs += gen(u, rng, ftype, T0 + start_day * DAY, stealth, inc)
            unreported[inc] = bool(rng.random() < SIM["unreported"])  # victim never reports: logged as legit
            inc += 1
        evs.sort(key=lambda e: e[0])
        st, rows = UserState(), []
        for ts, ev, (y, incident, stealth_v, tags, atype) in evs:
            day = (ts - T0) / DAY
            if day >= 0:
                rows.append(featurize(ev, st))
                meta["day"].append(day); meta["user"].append(uid); meta["y"].append(y); meta["incident"].append(incident)
                meta["stealth"].append(stealth_v); meta["amount"].append(ev.amount); meta["atype"].append(atype)
                meta["user_kind"].append(u.kind); meta["user_rate"].append(u.rate); meta["user_median"].append(u.median)
                meta["tag"].append("attack" if y else next((t for t in order if t in tags), "routine"))
            st.update(ts, ev.payee, ev.amount, ev.device_id)
        if rows:
            X.append(np.asarray(rows, dtype=np.float32))
    out = {"X": np.vstack(X)}
    for k, v in meta.items():
        out[k] = np.asarray(v)
    out["family"] = np.asarray([FAMILY.get(a, "legit") for a in out["atype"]])
    # y = ground truth (test evaluation); y_obs = what a bank would have labelled (some incidents are never reported)
    out["y_obs"] = np.array([0 if (yy and unreported.get(i, False)) else yy for yy, i in zip(out["y"], out["incident"])])
    out["features"] = np.asarray(FEATURES)
    return out
