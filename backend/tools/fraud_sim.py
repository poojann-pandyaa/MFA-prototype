"""
Scam-scenario simulator + evaluation harness for services/txn_risk.py.

Run from backend/:   .venv/bin/python -m tools.fraud_sim [--users 400] [--seed 0]

IMPORTANT - what this does and does not tell you:
  * Scenarios S1-S10 encode the HYPOTHESES in docs/upi-fraud-scenarios-deep-dive.md
    (which signals each scam would show in a payment flow). The scorer's weights
    were written from the same hypotheses, so high recall here is partly circular.
  * Base rates, noise and "legitimate lookalike" mixes are my assumptions, not
    measurements. Read the output as a regression/plumbing check and as a map of
    which scenarios the signals CAN'T see (look at S2, S9), not as real-world
    accuracy. Real accuracy needs labelled data from a bank/PSP.
"""
import argparse
import datetime
import math
import random
from collections import defaultdict
from typing import Callable, Dict, List, Tuple

from services.txn_risk import (
    Txn, assess, build_profile, LOW, MEDIUM, HIGH, CRITICAL,
)

NOW = datetime.datetime(2026, 11, 6, 8, 0)  # 13:30 IST, a normal daytime moment


def _utc_for_local_hour(day_offset: float, local_hour: float) -> datetime.datetime:
    base = (NOW - datetime.timedelta(days=day_offset)).replace(hour=0, minute=0)
    return base + datetime.timedelta(hours=local_hour) - datetime.timedelta(hours=5, minutes=30)


class SimUser:
    def __init__(self, rng: random.Random, cold: bool):
        self.rng = rng
        self.median = math.exp(rng.uniform(math.log(80), math.log(1500)))  # INR
        self.balance = self.median * rng.uniform(30, 400)
        self.regulars = [f"reg{rng.randrange(10**9)}@upi" for _ in range(rng.randint(5, 14))]
        n = rng.randint(0, 4) if cold else rng.randint(40, 150)
        self.history: List[Tuple[str, float, datetime.datetime]] = []
        for _ in range(n):
            payee = rng.choice(self.regulars) if rng.random() < 0.8 else f"shop{rng.randrange(10**9)}@upi"
            amount = self.median * math.exp(rng.gauss(0, 0.8))
            hour = min(23.9, max(5.0, rng.gauss(14, 4)))
            self.history.append((payee, amount, _utc_for_local_hour(rng.uniform(2, 90), hour)))

    def amount(self, lo: float, hi: float) -> float:
        return self.median * self.rng.uniform(lo, hi)

    def new_vpa(self) -> str:
        return f"new{self.rng.randrange(10**9)}@upi"

    def txn(self, payee: str, amount: float, **kw) -> Txn:
        kw.setdefault("balance", self.balance)
        return Txn(payee_vpa=payee, amount=amount, now=kw.pop("now", NOW), **kw)


def _chance(rng, p):
    return rng.random() < p


# ---- legitimate behaviour, including hard lookalikes -----------------------------------------
def legit_routine(u: SimUser):      return u.txn(u.rng.choice(u.regulars), u.amount(0.3, 2.0)), []
def legit_new_merchant(u: SimUser): return u.txn(u.new_vpa(), u.amount(0.4, 2.5), payee_category="merchant", payee_age_days=400, payee_distinct_payers_24h=u.rng.randint(0, 6)), []
def legit_large_oneoff(u: SimUser): return u.txn(u.new_vpa(), u.amount(5, 15), payee_age_days=300, call_active=_chance(u.rng, 0.1)), []
def legit_new_phone(u: SimUser):    return u.txn(u.rng.choice(u.regulars), u.amount(0.3, 2.0), device_bound_hours=u.rng.uniform(0, 20), old_device_active=False), []
def legit_collect_contact(u: SimUser): return u.txn(u.rng.choice(u.regulars), u.amount(0.3, 3), channel="collect", requester_is_contact=True), []
def legit_call_known(u: SimUser):   return u.txn(u.rng.choice(u.regulars), u.amount(0.3, 2.5), call_active=True, call_minutes=u.rng.uniform(1, 15)), []
def legit_call_new_payee(u: SimUser): return u.txn(u.new_vpa(), u.amount(0.5, 2.0), call_active=True, call_minutes=u.rng.uniform(1, 10), payee_age_days=200), []

LEGIT: Dict[str, Tuple[float, Callable]] = {
    "routine": (0.72, legit_routine), "new_merchant": (0.12, legit_new_merchant),
    "large_oneoff": (0.04, legit_large_oneoff), "new_phone": (0.02, legit_new_phone),
    "collect_from_contact": (0.04, legit_collect_contact), "call_known_payee": (0.04, legit_call_known),
    "call_new_payee": (0.02, legit_call_new_payee),
}


# ---- scam scenarios (return (txn_to_score, extra_history_rows)) --------------------------------
def _fresh_payee_stats(u: SimUser, **kw):
    return dict(payee_age_days=kw.get("age", u.rng.uniform(0, 5)),
                payee_distinct_payers_24h=u.rng.randint(0, 6))

def s1_collect(u):
    r = u.rng
    return u.txn(u.new_vpa(), u.amount(0.5, 3), channel="collect", call_active=_chance(r, 0.4),
                 call_unknown_number=True, **_fresh_payee_stats(u)), []

def s2_fake_qr(u):
    r = u.rng
    return u.txn(u.new_vpa(), u.amount(1, 4), channel="qr", call_active=_chance(r, 0.2), **_fresh_payee_stats(u)), []

def s3_fake_care(u):
    r = u.rng
    return u.txn(u.new_vpa(), u.amount(1, 5), channel="collect" if _chance(r, 0.4) else "pay",
                 call_active=_chance(r, 0.9), call_unknown_number=_chance(r, 0.8), call_minutes=r.uniform(5, 40),
                 remote_access_active=_chance(r, 0.5), **_fresh_payee_stats(u)), []

def s4_remote_access(u):
    r = u.rng
    return u.txn(u.new_vpa() if _chance(r, 0.9) else r.choice(u.regulars), u.amount(2, 10),
                 remote_access_active=_chance(r, 0.8), call_active=_chance(r, 0.6), call_minutes=r.uniform(5, 40),
                 **_fresh_payee_stats(u)), []

def s5_apk_binding(u):
    r = u.rng
    return u.txn(u.new_vpa(), u.amount(3, 15), device_bound_hours=r.uniform(0.1, 20), old_device_active=_chance(r, 0.7),
                 recent_sideload_hours=r.uniform(1, 40) if _chance(r, 0.8) else None,
                 now=_utc_for_local_hour(0, r.choice([2, 3, 4, 13, 15, 22])), **_fresh_payee_stats(u)), []

def s6_sim_swap(u):
    r = u.rng
    return u.txn(u.new_vpa(), u.amount(3, 15), sim_changed_hours=r.uniform(0.5, 60),
                 device_bound_hours=r.uniform(0, 12) if _chance(r, 0.8) else None, **_fresh_payee_stats(u)), []

def s7_digital_arrest(u):
    r = u.rng
    amount = max(u.amount(10, 40), u.balance * r.uniform(0.4, 0.9))
    return u.txn(u.new_vpa(), amount, call_active=True, call_minutes=r.uniform(25, 300),
                 call_unknown_number=_chance(r, 0.7), **_fresh_payee_stats(u)), []

def _seq_history(u: SimUser, n_prior: int, payees: int, lo: float, hi: float, hours: float):
    r, rows, vpas = u.rng, [], [u.new_vpa() for _ in range(payees)]
    amt = u.amount(lo, hi)
    for i in range(n_prior):
        amt *= r.uniform(1.0, 1.8)
        rows.append((vpas[i % payees], amt, NOW - datetime.timedelta(hours=hours * (n_prior - i) / (n_prior + 1))))
    return rows, vpas, amt

def s8_task_investment(u):
    r = u.rng
    rows, vpas, amt = _seq_history(u, r.randint(2, 3), r.randint(1, 3), 1, 4, 40)
    final = vpas[0] if _chance(r, 0.5) else u.new_vpa()
    return u.txn(final, amt * r.uniform(1.0, 1.8), payee_category=r.choice(["crypto", "gaming", "individual"]),
                 call_active=_chance(r, 0.1), **_fresh_payee_stats(u)), rows

def s9_extortion(u):
    r = u.rng
    rows, vpas, amt = _seq_history(u, r.randint(3, 5), 2, 0.5, 2, 20)
    return u.txn(vpas[-1] if _chance(r, 0.5) else u.new_vpa(), amt, now=_utc_for_local_hour(0, r.choice([1, 2, 3, 23])),
                 **_fresh_payee_stats(u)), rows

def s10_mule_payee(u):
    r = u.rng
    fri = r.choices(["none", "medium", "high", "very_high"], [0.15, 0.25, 0.35, 0.25])[0]
    return u.txn(u.new_vpa() if _chance(r, 0.7) else r.choice(u.regulars), u.amount(0.5, 3), payee_fri=fri,
                 payee_age_days=r.uniform(0, 5), payee_distinct_payers_24h=r.randint(5, 60),
                 payee_complaints=r.choice([0, 0, 1, 2, 4])), []

SCAMS: Dict[str, Callable] = {
    "S1 collect request": s1_collect, "S2 fake QR": s2_fake_qr, "S3 fake customer care": s3_fake_care,
    "S4 remote access": s4_remote_access, "S5 malicious APK binding": s5_apk_binding, "S6 SIM swap": s6_sim_swap,
    "S7 digital arrest": s7_digital_arrest, "S8 task/investment": s8_task_investment,
    "S9 extortion": s9_extortion, "S10 mule payee": s10_mule_payee,
}
LEVEL_RANK = {LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3}


def run(users: int = 400, seed: int = 0) -> Dict:
    rng = random.Random(seed)
    sim_users = [SimUser(rng, cold=(i % 8 == 0)) for i in range(users)]  # 1 in 8 is a cold-start user
    out = {"legit": defaultdict(lambda: [0, 0, 0]), "scam": defaultdict(lambda: [0, 0, 0, 0.0, 0.0, 0.0])}
    names, weights = zip(*[(k, v[0]) for k, v in LEGIT.items()])

    for u in sim_users:
        for _ in range(25):  # 25 legit probes per user
            kind = rng.choices(names, weights)[0]
            t, extra = LEGIT[kind][1](u)
            d = assess(t, build_profile(u.history + extra, t.now))
            row = out["legit"][kind]
            row[0] += 1; row[1] += LEVEL_RANK[d.severity] >= 1; row[2] += LEVEL_RANK[d.severity] >= 2
        for name, fn in SCAMS.items():
            t, extra = fn(u)
            d = assess(t, build_profile(u.history + extra, t.now))
            row = out["scam"][name]
            row[0] += 1; row[1] += LEVEL_RANK[d.severity] >= 1; row[2] += LEVEL_RANK[d.severity] >= 2
            row[3] += t.amount; row[4] += t.amount * (LEVEL_RANK[d.severity] >= 1); row[5] += t.amount * (LEVEL_RANK[d.severity] >= 2)
    return out


def summarise(out: Dict) -> Dict:
    n = sum(r[0] for r in out["legit"].values())
    flat = {k: (r[1] / r[0], r[2] / r[0]) for k, r in out["scam"].items()}
    return {
        "legit_flag_rate": sum(r[1] for r in out["legit"].values()) / n,
        "legit_friction_rate": sum(r[2] for r in out["legit"].values()) / n,  # >= HIGH: face check / hold / block
        "scam_recall": flat,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    out = run(a.users, a.seed)
    s = summarise(out)
    print(f"\nLEGIT payments ({sum(r[0] for r in out['legit'].values())} probes)  [assumed mix, see module docstring]")
    print(f"{'type':24}{'n':>7}{'>=MEDIUM':>10}{'>=HIGH':>9}")
    for k, r in out["legit"].items():
        print(f"{k:24}{r[0]:>7}{r[1]/r[0]:>10.1%}{r[2]/r[0]:>9.1%}")
    print(f"{'ALL legit':24}{'':>7}{s['legit_flag_rate']:>10.1%}{s['legit_friction_rate']:>9.1%}")
    print(f"\nSCAM scenarios [synthetic: encodes the same hypotheses the weights came from]")
    print(f"{'scenario':26}{'n':>6}{'>=MED':>8}{'>=HIGH':>8}{'rupees>=MED':>13}{'rupees>=HIGH':>14}")
    for k, r in out["scam"].items():
        print(f"{k:26}{r[0]:>6}{r[1]/r[0]:>8.0%}{r[2]/r[0]:>8.0%}{r[4]/r[3]:>13.0%}{r[5]/r[3]:>14.0%}")


if __name__ == "__main__":
    main()
