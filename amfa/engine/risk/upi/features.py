# Ported unchanged from branch Research/face-auth @ 2b057c5, backend/ato/features.py (DemoPay's UPI scam model).
# Only the import lines differ. See amfa/engine/risk/upi/__init__.py.
"""
Payment-risk features, shared by training and serving (takeover, coached-scam and bad-payee risks).

There is exactly one featurizer. The simulator (ato/simulate.py) and the /txn/risk endpoint both
build a `UserState` by calling `UserState.update` over the user's past payments and call `featurize`
on the new `Event`, so there is no train/serve skew by construction (tests/test_ato_model.py checks it).

Two feature groups, because they have different trust properties:
  SERVER  facts the PSP/bank/telco/backend can establish without trusting the phone
          (amounts, payee stats, velocity, binding/SIM/PIN-reset events, IP geo, payment type, FRI band).
  DEVICE  telemetry from an attested on-device SDK (remote-access flag, behavioural biometrics,
          integrity verdict, call-in-progress, contact status of a requester ...). If the attacker
          controls the device these can be forged or withheld, so a `server_only` variant exists too.
"""
import difflib
import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

NAN = float("nan")
IST_SECONDS = 19800
DAY = 86400.0
WINDOW = 3 * DAY  # state keeps 72h of recent payments (task/investment scams run over days)

# name -> group
FEATURE_GROUPS = {
    # amount / behaviour
    "log_amount": "server", "amt_z": "server", "amt_over_max": "server", "amt_over_p95": "server",
    "amt_over_balance": "server",
    # payee
    "new_payee": "server", "payee_age_days": "server", "payee_fan_in_24h": "server", "new_payees_24h": "server",
    "new_payees_72h": "server", "escalating_to_payee": "server", "payee_lookalike": "server",
    "payee_is_merchant": "server", "payee_risky_category": "server",
    # payee-side intelligence (government fraud-risk band, complaints)
    "payee_fri": "server", "payee_complaints": "server",
    # payment type
    "channel_collect": "server", "channel_qr": "server",
    # velocity
    "n_txn_1h": "server", "n_txn_24h": "server", "outflow_24h_over_balance": "server", "log_secs_since_last": "server",
    # time
    "hour_sin": "server", "hour_cos": "server", "hour_prob": "server", "night_share_user": "server", "is_night": "server",
    # history / device identity
    "hist_n": "server", "device_known": "server", "device_age_days": "server",
    # binding / SIM / PIN events (PSP + telco facts)
    "device_bound_hours": "server", "old_device_active": "server", "sim_changed_hours": "server", "pin_reset_hours": "server",
    "failed_pin_attempts": "server", "ip_geo_km": "server",
    # attested on-device telemetry
    "remote_access_active": "device", "bio_similarity": "device", "integrity_fail": "device",
    "overlay_accessibility": "device", "sideload_hours": "device", "login_to_pay_s": "device",
    "call_active": "device", "call_minutes": "device", "call_unknown_number": "device", "requester_is_contact": "device",
}
FEATURES = list(FEATURE_GROUPS)
SERVER_FEATURES = [f for f in FEATURES if FEATURE_GROUPS[f] == "server"]
DEVICE_FEATURES = [f for f in FEATURES if FEATURE_GROUPS[f] == "device"]
INDEX = {f: i for i, f in enumerate(FEATURES)}

# Features whose risk direction is unambiguous; enforced as monotone constraints so a
# model can't learn an exploitable "more suspicious signal => safer" dip from sparse data.
MONOTONE = {
    "remote_access_active": 1, "integrity_fail": 1, "overlay_accessibility": 1, "bio_similarity": -1,
    "failed_pin_attempts": 1, "ip_geo_km": 1, "new_payee": 1, "payee_fan_in_24h": 1, "payee_age_days": -1,
    "device_known": -1, "n_txn_1h": 1, "device_bound_hours": -1, "sim_changed_hours": -1,
    "pin_reset_hours": -1, "sideload_hours": -1, "payee_fri": 1, "payee_complaints": 1, "payee_lookalike": 1,
}


@dataclass
class Event:
    """One payment attempt, before the PIN screen. None = not observed."""
    ts: float                                   # UTC epoch seconds
    payee: str
    amount: float
    device_id: str
    balance: Optional[float] = None
    channel: str = "pay"                        # pay | collect | qr
    # server / PSP / telco facts
    payee_age_days: Optional[float] = None      # age of the payee on the platform (cross-user); None = never seen
    payee_fan_in_24h: int = 0                   # distinct OTHER payers who paid this payee in 24h
    payee_is_merchant: Optional[bool] = None
    payee_risky_category: Optional[bool] = None  # crypto / gaming style merchant category
    payee_fri: Optional[int] = None             # government fraud-risk band: 0 none, 1 medium, 2 high, 3 very high
    payee_complaints: Optional[int] = None
    device_bound_hours: Optional[float] = None  # age of this device's UPI binding; None = long-standing
    old_device_active: Optional[bool] = None    # an earlier binding is still online on another device
    sim_changed_hours: Optional[float] = None
    pin_reset_hours: Optional[float] = None
    failed_pin_attempts: Optional[int] = None
    ip_geo_km: Optional[float] = None           # IP-geolocation distance from the user's usual place
    # attested device telemetry
    remote_access_active: Optional[bool] = None
    bio_similarity: Optional[float] = None      # 0..1 similarity of touch/typing to the user's template
    integrity_fail: Optional[bool] = None
    overlay_accessibility: Optional[bool] = None
    sideload_hours: Optional[float] = None
    login_to_pay_s: Optional[float] = None
    call_active: Optional[bool] = None          # a phone/video call is in progress (flag + minutes only)
    call_minutes: Optional[float] = None
    call_unknown_number: Optional[bool] = None
    requester_is_contact: Optional[bool] = None  # for collect requests: is the requester a saved contact


def local_hour(ts: float) -> int:
    return int(((ts + IST_SECONDS) % DAY) // 3600)


def lookalike_score(payee: str, known) -> float:
    """Highest similarity (0..1) between `payee` and any other payee the user already pays."""
    best = 0.0
    for k in known:
        if k == payee or abs(len(k) - len(payee)) > 3:
            continue
        sm = difflib.SequenceMatcher(None, payee, k)
        if sm.real_quick_ratio() < 0.8 or sm.quick_ratio() < 0.8:
            continue
        best = max(best, sm.ratio())
    return best


class UserState:
    """Rolling per-user profile; O(1) update. Replay a user's history through `update`."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0      # Welford running mean / M2 of log1p(amount)
        self.m2 = 0.0
        self.max_amt = 0.0
        self.hours = [0] * 24
        self.payee_first = {}
        self.payee_last_amount = {}
        self.device_first = {}
        self.recent = deque()  # (ts, amount, payee) within the last 72h
        self.last_ts = None

    def update(self, ts: float, payee: str, amount: float, device_id: str) -> None:
        x = math.log1p(amount)
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)
        self.max_amt = max(self.max_amt, amount)
        self.hours[local_hour(ts)] += 1
        self.payee_first.setdefault(payee, ts)
        self.payee_last_amount[payee] = amount
        self.device_first.setdefault(device_id, ts)
        self.recent.append((ts, amount, payee))
        while self.recent and self.recent[0][0] < ts - WINDOW:
            self.recent.popleft()
        self.last_ts = ts

    def std(self) -> float:
        return max(math.sqrt(self.m2 / (self.n - 1)), 0.35) if self.n >= 2 else 1.0


def _f(v) -> float:
    return NAN if v is None else float(v)


def featurize(ev: Event, st: UserState) -> list:
    """Feature vector (FEATURES order) for `ev`, given the user's state BEFORE this payment."""
    lh = local_hour(ev.ts)
    la = math.log1p(ev.amount)
    warm = st.n >= 5
    p95 = math.expm1(st.mean + 1.645 * st.std()) if warm else NAN
    recent72 = [r for r in st.recent if r[0] > ev.ts - WINDOW]
    recent = [r for r in recent72 if r[0] > ev.ts - DAY]
    new_payee = ev.payee not in st.payee_first
    new_24 = {r[2] for r in recent if st.payee_first.get(r[2], -1e18) >= ev.ts - DAY}
    new_72 = {r[2] for r in recent72 if st.payee_first.get(r[2], -1e18) >= ev.ts - WINDOW}
    if new_payee:
        new_24.add(ev.payee)
        new_72.add(ev.payee)
    first = st.payee_first.get(ev.payee)
    escalating = first is not None and first >= ev.ts - WINDOW and ev.amount > st.payee_last_amount.get(ev.payee, math.inf)
    outflow = sum(r[1] for r in recent) + ev.amount
    bal = ev.balance if ev.balance else None
    dev_first = st.device_first.get(ev.device_id)

    v = {
        "log_amount": la,
        "amt_z": (la - st.mean) / st.std() if warm else NAN,
        "amt_over_max": ev.amount / st.max_amt if st.max_amt > 0 else NAN,
        "amt_over_p95": ev.amount / max(p95, 1.0) if warm else NAN,
        "amt_over_balance": ev.amount / bal if bal else NAN,
        "new_payee": float(new_payee),
        "payee_age_days": _f(ev.payee_age_days),
        "payee_fan_in_24h": float(ev.payee_fan_in_24h),
        "new_payees_24h": float(len(new_24)),
        "new_payees_72h": float(len(new_72)),
        "escalating_to_payee": float(escalating),
        "payee_lookalike": lookalike_score(ev.payee, st.payee_first) if new_payee else 0.0,
        "payee_is_merchant": _f(ev.payee_is_merchant),
        "payee_risky_category": _f(ev.payee_risky_category),
        "payee_fri": _f(ev.payee_fri),
        "payee_complaints": _f(ev.payee_complaints),
        "channel_collect": float(ev.channel == "collect"),
        "channel_qr": float(ev.channel == "qr"),
        "n_txn_1h": float(1 + sum(1 for r in recent if r[0] > ev.ts - 3600)),
        "n_txn_24h": float(1 + len(recent)),
        "outflow_24h_over_balance": outflow / bal if bal else NAN,
        "log_secs_since_last": math.log1p(max(ev.ts - st.last_ts, 0.0)) if st.last_ts is not None else NAN,
        "hour_sin": math.sin(2 * math.pi * lh / 24),
        "hour_cos": math.cos(2 * math.pi * lh / 24),
        "hour_prob": (st.hours[lh] + 1) / (st.n + 24),
        "night_share_user": sum(st.hours[:5]) / st.n if st.n else NAN,
        "is_night": float(lh < 5),
        "hist_n": float(st.n),
        "device_known": float(dev_first is not None),
        "device_age_days": (ev.ts - dev_first) / DAY if dev_first is not None else 0.0,
        "device_bound_hours": _f(ev.device_bound_hours),
        "old_device_active": _f(ev.old_device_active),
        "sim_changed_hours": _f(ev.sim_changed_hours),
        "pin_reset_hours": _f(ev.pin_reset_hours),
        "failed_pin_attempts": _f(ev.failed_pin_attempts),
        "ip_geo_km": _f(ev.ip_geo_km),
        "remote_access_active": _f(ev.remote_access_active),
        "bio_similarity": _f(ev.bio_similarity),
        "integrity_fail": _f(ev.integrity_fail),
        "overlay_accessibility": _f(ev.overlay_accessibility),
        "sideload_hours": _f(ev.sideload_hours),
        "login_to_pay_s": _f(ev.login_to_pay_s),
        "call_active": _f(ev.call_active),
        "call_minutes": _f(ev.call_minutes),
        "call_unknown_number": _f(ev.call_unknown_number),
        "requester_is_contact": _f(ev.requester_is_contact) if ev.channel == "collect" else NAN,
    }
    return [v[f] for f in FEATURES]
