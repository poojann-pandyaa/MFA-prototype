"""Bridges between DemoPay's simulator (upi/simulate.py) and the platform's risk inputs (risk/features.py).

    upi_streams(n, seed)      DemoPay's payment streams, exactly as its build_dataset draws them (same random numbers)
    to_platform(...)          one DemoPay payment -> the platform's Event, Profile and phone/payee signals
    to_upi(sim)               one platform payment -> a DemoPay Event (to score the platform's data with DemoPay's model)
    family_of(scenario)       the fraud family of any attack in either simulator

Every mapping below is a stated assumption, kept small: a fact one side records and the other does not is left missing,
never invented.
"""
from __future__ import annotations

from typing import Iterator, Optional

import numpy as np

from ..features import Event, Profile
from . import simulate as S
from .features import DAY, Event as UpiEvent, local_hour

H = 3600.0
FAR_KM = 50.0          # an IP more than this far from the customer's usual place counts as a different network

# The fraud family each attack belongs to. DemoPay's three families, plus the platform's first-party fraud (the account
# holder is the fraudster), which DemoPay does not simulate.
FAMILIES = ("UNAUTHORISED", "COACHED", "PAYEE_RISK", "FIRST_PARTY")
_UPI_FAMILY = {"takeover": "UNAUTHORISED", "coached": "COACHED", "payee": "PAYEE_RISK"}
PLATFORM_FAMILY = {
    # someone else is operating the account
    "account_takeover": "UNAUTHORISED", "sim_swap": "UNAUTHORISED", "remote_access": "UNAUTHORISED", "mule_fanout": "UNAUTHORISED",
    "bot": "UNAUTHORISED", "dormant_takeover": "UNAUTHORISED", "credential_stuffing": "UNAUTHORISED", "card_testing": "UNAUTHORISED",
    "test_then_drain": "UNAUTHORISED", "slow_drip": "UNAUTHORISED", "beneficiary_abuse": "UNAUTHORISED",
    "limit_raise_drain": "UNAUTHORISED", "device_binding_takeover": "UNAUTHORISED", "profile_change_takeover": "UNAUTHORISED",
    "impossible_travel": "UNAUTHORISED", "low_and_slow_mimic": "UNAUTHORISED", "stolen_device": "UNAUTHORISED",
    "patient_helper": "UNAUTHORISED", "recurring_amount_jump": "UNAUTHORISED",
    # the real customer, tricked into paying
    "scam_victim": "COACHED", "repeat_scam": "COACHED",
    # the receiving account is the problem
    "flagged_payee": "PAYEE_RISK", "bill_payee_swap": "PAYEE_RISK",
    # the account holder is the fraudster
    "bustout": "FIRST_PARTY", "new_account_fraud": "FIRST_PARTY", "customer_as_mule": "FIRST_PARTY",
}


def family_of(scenario: str) -> Optional[str]:
    """Family of an attack scenario: the platform's names, or DemoPay's prefixed "upi:" (None for a genuine scenario)."""
    if scenario.startswith("upi:"):
        fam = S.FAMILY.get(scenario[4:])
        return _UPI_FAMILY.get(fam) if fam else None
    return PLATFORM_FAMILY.get(scenario)


def upi_streams(n_users: int = 6000, seed: int = 7, attacked_frac: float = 0.25) -> Iterator[tuple]:
    """Yields (uid, user, events, unreported) per simulated user, drawing the same random numbers as S.build_dataset, so the
    streams are exactly the ones DemoPay's model was trained and tested on. events: [(ts, Event, (y, incident, stealth,
    tags, atype))], sorted, including the warm-up before day 0. unreported: incidents the victim never reported."""
    rng = np.random.default_rng(seed)
    merchants = [S.Payee(S.new_vpa(rng), S.T0 - float(rng.uniform(60, 2500)) * DAY, int(rng.integers(5, 200)), True,
                         bool(rng.random() < 0.02), S._legit_fri(rng), S._legit_complaints(rng)) for _ in range(2000)]
    mix = S.SIM["mix"] or S.MIX
    types, weights = list(mix), np.array(list(mix.values()))
    inc = 0
    for uid in range(n_users):
        u = S.make_user(rng, uid)
        u.base_bal = u.median * float(rng.uniform(25, 300))
        evs = S.gen_legit(u, rng, merchants)
        unreported = {}
        if rng.random() < attacked_frac:
            ftype = types[int(rng.choice(len(types), p=weights / weights.sum()))]
            r = rng.random()
            stealth = float(rng.uniform(0, 0.3) if r < 0.5 else rng.uniform(0.3, 0.6) if r < 0.8 else rng.uniform(0.65, 1.0))
            start_day = float(rng.uniform(max(u.join_day + 8, 0.0), S.TOTAL_DAYS - 3))
            if start_day > 110:
                stealth = min(1.0, stealth + 0.15)
            stealth = min(1.0, stealth + S.SIM["stealth_shift"])
            gen = S.gen_takeover if ftype in S.TAKEOVER else S.gen_coached
            evs += gen(u, rng, ftype, S.T0 + start_day * DAY, stealth, inc)
            unreported[inc] = bool(rng.random() < S.SIM["unreported"])
            inc += 1
        evs.sort(key=lambda e: e[0])
        yield uid, u, evs, unreported


def _b(v) -> Optional[float]:
    return None if v is None else float(v)


def signals_of(ev: UpiEvent) -> dict:
    """The phone signals and payee intelligence DemoPay records for one payment, under the platform's SIGNALS names, and the
    account balance (features.CONTEXT_SIGNALS)."""
    collect = ev.channel == "collect"
    return {
        "call_active": _b(ev.call_active), "call_minutes": _b(ev.call_minutes), "call_unknown_number": _b(ev.call_unknown_number),
        "remote_access_active": _b(ev.remote_access_active), "overlay_accessibility": _b(ev.overlay_accessibility),
        "integrity_fail": _b(ev.integrity_fail), "bio_similarity": _b(ev.bio_similarity), "sideload_hours": _b(ev.sideload_hours),
        "login_to_pay_s": _b(ev.login_to_pay_s), "requester_is_contact": _b(ev.requester_is_contact) if collect else None,
        "payee_account_age_days": _b(ev.payee_age_days), "payee_fan_in_24h": _b(ev.payee_fan_in_24h),
        "payee_is_merchant": _b(ev.payee_is_merchant), "payee_risky_category": _b(ev.payee_risky_category),
        "payee_fri": _b(ev.payee_fri), "payee_complaints": _b(ev.payee_complaints),
        "channel_collect": float(collect), "channel_qr": float(ev.channel == "qr"),
        "device_bound_hours": _b(ev.device_bound_hours), "old_device_active": _b(ev.old_device_active),
        "failed_pin_attempts": _b(ev.failed_pin_attempts), "ip_geo_km": _b(ev.ip_geo_km),
        "balance": _b(ev.balance),
    }


def to_platform(ts: float, ev: UpiEvent, fraud: bool, u: "S.User") -> tuple[Event, Profile, dict]:
    """One DemoPay payment as the platform sees it.

    Assumptions: a far-away IP is a different network; a payment in a fraud incident is not a verified one (so it does not
    teach the customer's "known" payees or devices, as in the platform's own simulator); a PIN reset is the platform's
    password reset; a payee in the top two government fraud-risk bands is on the bank's risk list. DemoPay has no
    customer segment, declared amounts or behaviour score, so those stay at their defaults (missing)."""
    far = ev.ip_geo_km is not None and ev.ip_geo_km > FAR_KM
    e = Event(at=ts, hour=local_hour(ts), amount=float(ev.amount), type="PAYMENT" if ev.channel == "qr" else "TRANSFER",
              channel="MOBILE", payee=ev.payee, device=ev.device_id, network=f"far-{int(ev.ip_geo_km // 250)}" if far else "home",
              place=None, country="IN", authorized=not fraud, failed_factors=int(ev.failed_pin_attempts or 0), spoof=False)
    p = Profile(segment="STANDARD", account_created_at=S.T0 + u.join_day * DAY, enrolled_factors=2,
                sim_changed_at=None if ev.sim_changed_hours is None else ts - ev.sim_changed_hours * H,
                password_reset_at=None if ev.pin_reset_hours is None else ts - ev.pin_reset_hours * H,
                payee_risk_flag=bool(ev.payee_fri is not None and ev.payee_fri >= 2))
    return e, p, signals_of(ev)


def to_upi(event: Event, profile: Profile) -> UpiEvent:
    """One platform payment as DemoPay's model would see it. The platform records no phone signals or payee intelligence
    beyond its risk-list flag, so those are missing."""
    hours = lambda at: None if at is None or at > event.at else (event.at - at) / H  # noqa: E731
    return UpiEvent(ts=event.at, payee=event.payee, amount=float(event.amount), device_id=event.device, channel="pay",
                    payee_fri=2 if profile.payee_risk_flag else None, sim_changed_hours=hours(profile.sim_changed_at),
                    pin_reset_hours=hours(profile.password_reset_at), failed_pin_attempts=event.failed_factors or None)
