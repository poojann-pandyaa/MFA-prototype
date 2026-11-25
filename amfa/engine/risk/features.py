"""Risk-model features. The same `build()` runs on simulated history at training time and on a
customer's stored transactions at decision time, so the two can never compute a feature differently.
Missing values are None (the model treats them as missing, e.g. no history yet)."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from .declared import PEER_K

TXN_TYPES = ["TRANSFER", "PAYMENT", "CARD_NOT_PRESENT", "ADD_BENEFICIARY", "CHANGE_LIMIT",
             "PROFILE_CHANGE", "DEVICE_BINDING"]
CHANNELS = ["MOBILE", "WEB", "BRANCH", "API"]
SEGMENTS = ["STANDARD", "PREMIUM", "SENIOR", "CORPORATE", "NEW_TO_BANK", "WATCHLIST"]
CATEGORICAL = {"txn_type": TXN_TYPES, "channel": CHANNELS, "segment": SEGMENTS}

# what each kind of action is: money moves, a payee is involved, or the account itself is being changed
MONETARY_TYPES = {"TRANSFER", "PAYMENT", "CARD_NOT_PRESENT"}
PAYEE_TYPES = MONETARY_TYPES | {"ADD_BENEFICIARY"}
SENSITIVE_TYPES = {"ADD_BENEFICIARY", "CHANGE_LIMIT", "PROFILE_CHANGE", "DEVICE_BINDING"}

# Nothing older than this is read, stored or needed: the bank may keep a customer's transactions for 90 days at most (see
# amfa/core/retention.py, which deletes older rows). Every "usual" below is learned from these 90 days.
HISTORY_DAYS = 90
CYCLES = (7, 14, 30, 60)   # weekly to two-monthly: what a repeating payment looks like inside the window
H, DAY = 3600.0, 86400.0

# +1: risk can only rise with the value, -1: only fall. Keeps explanations sensible and stops an
# attacker lowering the score by pushing a value further in the "risky" direction.
MONOTONE = {
    "amount_ratio": 1, "amount_vs_max": 1, "amount_24h_ratio": 1, "amount_z_self": 1, "payee_z": 1, "habit_surprise": 1, "amount_vs_declared": 1, "declared_gap": 1, "velocity_1h": 1, "velocity_24h": 1,
    "new_payees_24h": 1, "sensitive_actions_24h": 1, "failed_factors_24h": 1, "spoof_7d": 1, "payee_risk_flag": 1,
    "payee_seen": -1, "amount_vs_payee_avg": 1,
    "device_seen": -1, "helper_device": -1, "network_seen": -1, "place_seen": -1,
    "sim_changed_hours": -1, "contact_changed_hours": -1, "password_reset_hours": -1,
    # DemoPay's directions for its signals (same rule: only where the direction is unambiguous)
    "remote_access_active": 1, "integrity_fail": 1, "overlay_accessibility": 1, "bio_similarity": -1, "failed_pin_attempts": 1,
    "ip_geo_km": 1, "payee_fan_in_24h": 1, "payee_account_age_days": -1, "device_bound_hours": -1, "sideload_hours": -1,
    "payee_fri": 1, "payee_complaints": 1, "amount_over_balance": 1, "outflow_24h_over_balance": 1, "new_payees_72h": 1,
}

CORE_FEATURES = [
    "amount_log", "amount_ratio", "amount_vs_max", "amount_24h_ratio", "amount_z_self", "payee_z", "habit_surprise", "amount_vs_declared", "declared_gap",
    "velocity_1h", "velocity_24h", "new_payees_24h", "sensitive_actions_24h",
    "payee_seen", "payee_age_days", "days_since_payee_last_paid", "amount_vs_payee_avg",
    "payee_gap_ratio", "payee_cycle_error", "device_seen", "device_age_days", "device_key_bound", "helper_device",
    "network_seen", "place_seen", "place_changed", "country_changed",
    "minutes_since_last", "days_since_last_ok", "hour_deviation", "is_night",
    "failed_factors_24h", "spoof_7d", "history_count", "account_age_days", "enrolled_factors",
    "ueba_risk", "sim_changed_hours", "contact_changed_hours", "password_reset_hours",
    "payee_risk_flag", "txn_type", "channel", "segment",
]

# From DemoPay (branch Research/face-auth): what the phone and the payment network can report about one payment. All are
# optional. A caller that does not have them passes nothing and they reach the model as missing, never as zero, so every
# existing caller behaves as before. See docs/merged-risk-model.md.
PHONE_SIGNALS = [
    "call_active", "call_minutes", "call_unknown_number",       # a call in progress while paying (flag and minutes, never content)
    "remote_access_active", "overlay_accessibility",            # a screen-sharing / remote-control app; an app drawing over the screen
    "integrity_fail", "sideload_hours",                         # a rooted or tampered phone; an app installed from outside the store
    "bio_similarity", "login_to_pay_s",                         # touch/typing like the owner's (0-1); seconds from sign-in to paying
    "requester_is_contact",                                     # a collect request from someone in the contacts
]
NETWORK_SIGNALS = [
    "payee_account_age_days", "payee_fan_in_24h",               # how old the receiving account is; how many people paid it today
    "payee_is_merchant", "payee_risky_category",
    "payee_fri", "payee_complaints",                            # the government fraud-risk band (0-3); complaints against the payee
    "channel_collect", "channel_qr",                            # a collect request or a QR payment
    "device_bound_hours", "old_device_active",                  # the payments app was bound to this phone recently; the old one still on
    "failed_pin_attempts", "ip_geo_km",                         # wrong PINs on this attempt; distance from the customer's usual place
]
SIGNALS = PHONE_SIGNALS + NETWORK_SIGNALS
# not model inputs themselves: facts the model's inputs are worked out from
CONTEXT_SIGNALS = ["balance"]                                   # the account balance before this payment (rupees), if the bank gives it
# worked out from the customer's own history (and the balance, when given), like the core features
DERIVED_FEATURES = [
    "escalating_to_payee",          # rising amounts to a payee first paid in the last 3 days (task and investment scams)
    "new_payees_72h",               # new payees in the last 3 days (scams that run over several days)
    "amount_over_balance",          # share of the balance this payment takes
    "outflow_24h_over_balance",     # share of the balance sent in the last 24 hours, this payment included
    "signals_present",              # 1 when the bank sent any phone or network signal: tells "not sent" apart from "all quiet"
]
FEATURES = CORE_FEATURES + DERIVED_FEATURES + SIGNALS


@dataclass(frozen=True)
class Event:
    """One transaction attempt: the one being decided, or a past one."""
    at: float
    hour: int
    amount: float
    type: str
    channel: str
    payee: str
    device: str
    network: str
    place: Optional[str] = None
    country: Optional[str] = None
    authorized: bool = True
    failed_factors: int = 0
    spoof: bool = False


@dataclass(frozen=True)
class Profile:
    """Facts about the customer and account at decision time."""
    segment: str = "STANDARD"
    account_created_at: Optional[float] = None
    enrolled_factors: int = 0
    device_key_bound: bool = False
    ueba_risk: Optional[float] = None
    sim_changed_at: Optional[float] = None
    contact_changed_at: Optional[float] = None
    password_reset_at: Optional[float] = None
    payee_risk_flag: bool = False
    # what the customer said at sign-up (rupees): a typical payment and roughly a month's total; None when they skipped it
    declared_typical: Optional[float] = None
    declared_month: Optional[float] = None
    # (centre, spread) of the habit score before they have any history: from their declaration, or their peer group (see core/peers.py)
    peer: Optional[tuple] = None
    # devices of family members the customer chose to let help (optional, set up from their own phone, in force after a cooling-off):
    # a payment from one of them is expected, not a stranger. Keyed hashes, same as Event.device.
    helpers: frozenset = frozenset()


def _hours_since(at: Optional[float], now: float) -> Optional[float]:
    return None if at is None or at > now else (now - at) / H


def hour_deviation(hour: int, past_hours: list[int]) -> Optional[float]:
    """Distance in hours from the customer's usual time of day, scaled by how regular they are."""
    if len(past_hours) < 5:
        return None
    n, mx, my = len(past_hours), 0.0, 0.0
    for h, k in Counter(past_hours).items():  # hours are whole numbers: at most 24 terms, not one per payment
        mx += k * math.cos(2 * math.pi * h / 24)
        my += k * math.sin(2 * math.pi * h / 24)
    mx, my = mx / n, my / n
    concentration = math.hypot(mx, my)
    if concentration < 1e-9:
        return 0.0
    this = 2 * math.pi * hour / 24
    diff = abs(math.atan2(math.sin(this - math.atan2(my, mx)), math.cos(this - math.atan2(my, mx))))
    return diff * 24 / (2 * math.pi) * concentration


def habit_z(x: float, past: list[float], *, min_n: int, floor: float, prior: float, k: float,
            centre: Optional[float] = None) -> Optional[float]:
    """How many "usual spreads" above this customer's own habit [x] is, in log-amount terms. The centre is the median of their
    own past amounts and the spread their own robust deviation (never below [floor]), blended with a generic spread [prior] that
    counts as [k] payments, so a customer with little history is not judged on a spread learned from two payments.
    None until there are [min_n] past amounts. Positive = bigger than usual; scale-free, so Rs 10,000 is a large jump for a
    customer who pays Rs 1,500 and nothing at all for one who pays Rs 1 lakh every week.
    With a [centre] (from what the customer declared or their peers) the median is pulled towards it with the weight of [k]
    payments, and it works from the first payment; their own payments outweigh it as they build up."""
    if len(past) < min_n and centre is None:
        return None
    n = len(past)
    logs = [math.log1p(v) for v in past]
    m = median(logs) if n else None
    mad = max(1.4826 * median(abs(v - m) for v in logs), floor) if n else 0.0
    if centre is not None:
        m = centre if not n else (n * m + k * centre) / (n + k)
    sigma = math.sqrt((n * mad ** 2 + k * prior ** 2) / (n + k))
    return (math.log1p(x) - m) / sigma


def exposure_24h(cur: Event, history: list[Event]) -> float:
    """Money moved or attempted: this payment plus everything in the last 24 hours."""
    return (cur.amount if cur.type in MONETARY_TYPES else 0.0) + sum(
        e.amount for e in history if e.type in MONETARY_TYPES and cur.at - DAY <= e.at < cur.at)


def build(cur: Event, history: list[Event], profile: Profile, signals: Optional[dict] = None) -> dict[str, Optional[float]]:
    """The model's inputs for one decision: the transaction `cur`, the customer's earlier events and the
    account facts. Only the last HISTORY_DAYS of history count, and only verified payments teach what is
    "known" (a payee, device, network or place). A value that cannot be known yet is None, which the
    model treats as missing, never as zero. [signals]: what the phone and the payment network report about
    this payment (keys in SIGNALS, plus the account "balance" in rupees; all optional; unknown keys are ignored).
    Returns exactly the keys in FEATURES."""
    now = cur.at
    since = now - HISTORY_DAYS * DAY
    has_payee, monetary = cur.type in PAYEE_TYPES, cur.type in MONETARY_TYPES
    amounts, hours = [], []
    v1h = v24h = failed24 = spoof7 = sens24 = 0
    amount24 = amount30 = 0.0
    payee_seen = device_seen = network_seen = place_seen = 0
    payee_first = device_first = None
    known_before_24h: set[str] = set()
    known_before_72h: set[str] = set()
    recent_payees = {cur.payee} if has_payee else set()
    paid_at: list[float] = []      # when this payee was verifiably paid in the window
    paid_amounts: list[float] = []
    last = last_ok = None

    for e in sorted(history, key=lambda e: e.at):
        if e.at >= now or e.at < since:
            continue
        age = now - e.at
        last = e
        if age <= H:
            v1h += 1
        if age <= DAY:
            v24h += 1
            failed24 += e.failed_factors
            if e.type in MONETARY_TYPES:
                amount24 += e.amount
            if e.type in PAYEE_TYPES:
                recent_payees.add(e.payee)
            sens24 += e.type in SENSITIVE_TYPES
        if age <= 7 * DAY and e.spoof:
            spoof7 += 1
        if not e.authorized:
            continue
        last_ok = e
        hours.append(e.hour)
        if e.type in MONETARY_TYPES:
            amounts.append(e.amount)
            if age <= 30 * DAY:
                amount30 += e.amount
        if age > DAY and e.type in PAYEE_TYPES:
            known_before_24h.add(e.payee)
        if age > 3 * DAY and e.type in PAYEE_TYPES:
            known_before_72h.add(e.payee)
        if has_payee and e.type in PAYEE_TYPES and e.payee == cur.payee:
            payee_seen += 1
            paid_at.append(e.at)
            if e.type in MONETARY_TYPES:
                paid_amounts.append(e.amount)
            payee_first = e.at if payee_first is None else payee_first
        if e.device == cur.device:
            device_seen += 1
            device_first = e.at if device_first is None else device_first
        if e.network == cur.network:
            network_seen += 1
        if cur.place is not None and e.place == cur.place:
            place_seen += 1

    gap = (now - paid_at[-1]) / DAY if paid_at else None           # days since this payee was last paid
    avg_gap = (paid_at[-1] - paid_at[0]) / DAY / (len(paid_at) - 1) if len(paid_at) > 1 else None
    avg_paid = sum(paid_amounts) / len(paid_amounts) if paid_amounts else None
    med = median(amounts) if amounts else None
    ratio = (lambda x: x / med) if med and monetary else (lambda x: None)
    peer = profile.peer
    z_self = (habit_z(cur.amount, amounts, min_n=0 if peer else 3, floor=.3, prior=peer[1] if peer else .9, k=PEER_K if peer else 5,
                      centre=peer[0] if peer else None) if monetary else None)
    z_payee = habit_z(cur.amount, paid_amounts, min_n=2, floor=.2, prior=.5, k=2) if monetary and has_payee else None
    # a payee paid before, at amounts like this one, is routine whatever the overall spread; otherwise judge against everything
    surprise = max(0.0, z_payee if z_payee is not None else z_self) if (z_payee is not None or z_self is not None) else None
    escalating = (int(payee_first is not None and now - payee_first <= 3 * DAY and bool(paid_amounts) and cur.amount > paid_amounts[-1])
                  if monetary and has_payee else None)
    sig = signals or {}
    balance = _num(sig.get("balance"))
    balance = balance if balance and balance > 0 else None
    recent_72h = {e.payee for e in history if e.type in PAYEE_TYPES and now - 3 * DAY <= e.at < now} | ({cur.payee} if has_payee else set())
    new_72h = len(recent_72h - known_before_72h)
    return {
        "escalating_to_payee": escalating,
        "new_payees_72h": new_72h,
        "amount_over_balance": cur.amount / balance if balance and monetary else None,
        "outflow_24h_over_balance": (amount24 + cur.amount) / balance if balance and monetary else None,
        "signals_present": int(any(_num(sig.get(k)) is not None for k in SIGNALS)),
        **{k: _num(sig.get(k)) for k in SIGNALS},
        "amount_z_self": z_self, "payee_z": z_payee, "habit_surprise": surprise,
        "amount_vs_declared": cur.amount / profile.declared_typical if monetary and profile.declared_typical else None,
        "declared_gap": (amount30 + cur.amount) / profile.declared_month if monetary and profile.declared_month else None,
        "amount_log": math.log1p(max(cur.amount, 0.0)) if monetary else None,
        "amount_ratio": ratio(cur.amount),
        "amount_vs_max": cur.amount / max(amounts) if monetary and amounts and max(amounts) > 0 else None,
        "amount_24h_ratio": ratio(amount24 + cur.amount),
        "velocity_1h": v1h,
        "velocity_24h": v24h,
        "new_payees_24h": len(recent_payees - known_before_24h),
        "sensitive_actions_24h": sens24,
        "payee_seen": payee_seen if has_payee else None,
        "payee_age_days": (now - payee_first) / DAY if payee_first is not None else None,
        "days_since_payee_last_paid": gap,
        "amount_vs_payee_avg": cur.amount / avg_paid if monetary and avg_paid else None,
        "payee_gap_ratio": gap / avg_gap if gap is not None and avg_gap else None,    # ~1: on the usual schedule
        "payee_cycle_error": min(abs(gap - c) / c for c in CYCLES) if gap is not None else None,
        "device_seen": device_seen,
        "device_age_days": (now - device_first) / DAY if device_first is not None else None,
        "device_key_bound": int(profile.device_key_bound),
        "helper_device": int(cur.device in profile.helpers),
        "network_seen": network_seen,
        "place_seen": place_seen if cur.place is not None else None,
        "place_changed": (int(cur.place != last.place)
                          if last is not None and cur.place and last.place else None),
        "country_changed": (int(cur.country != last.country)
                            if last is not None and cur.country and last.country else None),
        "minutes_since_last": (now - last.at) / 60 if last is not None else None,
        "days_since_last_ok": (now - last_ok.at) / DAY if last_ok is not None else None,
        "hour_deviation": hour_deviation(cur.hour, hours),
        "is_night": int(0 <= cur.hour < 6),
        "failed_factors_24h": failed24,
        "spoof_7d": spoof7,
        "history_count": len(amounts),
        "account_age_days": ((now - profile.account_created_at) / DAY
                             if profile.account_created_at is not None else None),
        "enrolled_factors": profile.enrolled_factors,
        "ueba_risk": profile.ueba_risk,
        "sim_changed_hours": _hours_since(profile.sim_changed_at, now),
        "contact_changed_hours": _hours_since(profile.contact_changed_at, now),
        "password_reset_hours": _hours_since(profile.password_reset_at, now),
        "payee_risk_flag": int(profile.payee_risk_flag),
        "txn_type": _code("txn_type", cur.type),
        "channel": _code("channel", cur.channel),
        "segment": _code("segment", profile.segment),
    }


def _num(v) -> Optional[float]:
    if v is None or isinstance(v, str):
        return None
    f = float(v)
    return None if f != f else f


def _code(name: str, value: str) -> Optional[int]:
    values = CATEGORICAL[name]
    return values.index(value) if value in values else None


def vector(features: dict[str, Optional[float]], names: Optional[list[str]] = None) -> list[float]:
    """The model's input row, in the order of [names] (default FEATURES). A model records the list it was trained on, so an
    older model trained before the phone and network signals existed still scores correctly."""
    return [math.nan if features.get(k) is None else float(features[k]) for k in (names or FEATURES)]
