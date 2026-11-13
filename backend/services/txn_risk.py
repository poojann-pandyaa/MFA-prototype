"""
Transaction-risk scorer for UPI-style payments (pure functions, no DB).

Scores one payment against the user's own history and returns a threat TYPE
and SEVERITY, then maps both to a response (the "ladder" in
docs/upi-fraud-model-plan.md section 4). Three threat types, because they
need different responses:

  UNAUTHORISED  account takeover (SIM swap, malware, new-device binding)  -> identity step-up
  COACHED       the real user is being deceived (digital arrest, collect
                request, fake support, task/investment scams)             -> warning / delay
  PAYEE_RISK    the receiving account/number looks like a mule            -> badge / hold / block

ponytail: hand-weighted noisy-OR evidence, not a trained model. The weights
encode the hypotheses in docs/upi-fraud-scenarios-deep-dive.md and have NOT
been fit to real labelled data (none is public). Replace with a calibrated
GBDT once a partner supplies labels; keep this as the baseline and fallback.
Device signals (call_active, remote_access_active, ...) are client-supplied
in this demo and therefore spoofable; in production they must come from an
attested SDK.
"""
import datetime
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

UNAUTHORISED, COACHED, PAYEE_RISK, NONE = "UNAUTHORISED", "COACHED", "PAYEE_RISK", "NONE"
LOW, MEDIUM, HIGH, CRITICAL = "LOW", "MEDIUM", "HIGH", "CRITICAL"
_LEVEL_THRESHOLDS = ((0.85, CRITICAL), (0.60, HIGH), (0.35, MEDIUM))  # checked in order

IST = datetime.timedelta(hours=5, minutes=30)  # profile hours are local (India) time
COLD_START_TXNS = 5          # below this, don't trust the user's own amount percentile
COLD_START_BASELINE = 5000.0  # INR, assumed typical ceiling for a user we know nothing about
BASELINE_FLOOR = 2000.0       # INR, so users with tiny history don't flag every normal payment
HOLD_MINUTES = 30

ACTIONS = {
    UNAUTHORISED: {MEDIUM: "device_biometric_reprompt", HIGH: "face_challenge", CRITICAL: "block_and_lock_binding"},
    COACHED: {MEDIUM: "scam_warning", HIGH: "cooling_off_hold", CRITICAL: "block_and_offer_report"},
    PAYEE_RISK: {MEDIUM: "payee_risk_badge", HIGH: "hold_and_verify_payee", CRITICAL: "block"},
}


@dataclass
class Txn:
    payee_vpa: str
    amount: float
    now: datetime.datetime                      # UTC, naive (matches the rest of the repo)
    channel: str = "pay"                        # pay | collect | qr
    balance: Optional[float] = None
    requester_is_contact: bool = False          # collect: is the requester a saved contact
    pasted_amount: bool = False
    payee_category: str = "individual"          # individual | merchant | crypto | gaming
    # device / session signals (attested SDK in production)
    call_active: bool = False
    call_minutes: float = 0.0
    call_unknown_number: bool = False
    remote_access_active: bool = False
    device_bound_hours: Optional[float] = None  # age of this device's UPI binding; None = long-standing
    old_device_active: bool = False             # a binding exists on another device that is still online
    sim_changed_hours: Optional[float] = None
    recent_sideload_hours: Optional[float] = None
    # payee-side intelligence (DoT FRI / MuleHunter / platform-local stats)
    payee_fri: str = "none"                     # none | medium | high | very_high
    mule_score: Optional[float] = None          # 0..1 from MuleHunter-style service, if available
    payee_complaints: int = 0
    payee_age_days: Optional[float] = None      # None = never seen on platform
    payee_distinct_payers_24h: int = 0


@dataclass
class Profile:
    n: int = 0
    p95_amount: float = 0.0
    known_payees: frozenset = frozenset()
    night_share: float = 0.0                         # share of txns made 00:00-04:59 local
    recent_new_payees_72h: Dict[str, float] = field(default_factory=dict)  # payee -> last amount, payees first seen < 72h ago
    last_amount_to: Dict[str, float] = field(default_factory=dict)         # payee -> latest amount
    first_seen: Dict[str, datetime.datetime] = field(default_factory=dict)


@dataclass
class Decision:
    threat_type: str
    severity: str
    score: float
    action: str
    reasons: List[Tuple[str, str]]               # (code, text); server-side only, never sent to the client
    class_scores: Dict[str, float]
    hold_minutes: int = 0


def build_profile(history: Sequence[Tuple[str, float, datetime.datetime]], now: datetime.datetime) -> Profile:
    """history: (payee_vpa, amount, utc timestamp) of the user's completed payments."""
    if not history:
        return Profile()
    rows = sorted(history, key=lambda r: r[2])
    amounts = sorted(r[1] for r in rows)
    p = Profile(n=len(rows), p95_amount=amounts[min(len(amounts) - 1, int(0.95 * len(amounts)))])
    p.night_share = sum(1 for r in rows if (r[2] + IST).hour < 5) / len(rows)
    for payee, amount, ts in rows:
        p.first_seen.setdefault(payee, ts)
        p.last_amount_to[payee] = amount
    p.known_payees = frozenset(p.first_seen)
    window_start = now - datetime.timedelta(hours=72)
    p.recent_new_payees_72h = {
        payee: p.last_amount_to[payee] for payee, seen in p.first_seen.items() if seen >= window_start
    }
    return p


def _noisy_or(items: List[Tuple[str, str, float]]) -> float:
    return 1 - math.prod(1 - w for _, _, w in items)


def _level(score: float) -> str:
    for threshold, level in _LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return LOW


def assess(t: Txn, p: Profile) -> Decision:
    new_payee = t.payee_vpa not in p.known_payees
    baseline = max(p.p95_amount, BASELINE_FLOOR) if p.n >= COLD_START_TXNS else COLD_START_BASELINE
    ratio = t.amount / baseline
    drain = t.amount / t.balance if t.balance else 0.0
    local_hour = (t.now + IST).hour
    odd_hour = local_hour < 5 and p.night_share < 0.05

    # new payees in the last 72h (task/investment scams run over days), counting this one
    prior_new = {k for k in p.recent_new_payees_72h if k != t.payee_vpa}
    seq = len(prior_new) + (1 if new_payee or t.payee_vpa in p.recent_new_payees_72h else 0)
    escalating = (
        t.payee_vpa in p.recent_new_payees_72h and t.amount > p.last_amount_to.get(t.payee_vpa, math.inf)
    )

    def ev(code, text, weight, active):
        return (code, text, weight) if active else None

    def tiered(code, text, value, tiers):  # tiers: [(threshold, weight)] ascending; the highest met wins
        met = [(th, w) for th, w in tiers if value >= th]
        return (code, text, met[-1][1]) if met else None

    amount_ev = tiered("high_amount", "amount far above this user's norm", ratio, [(3, 0.25), (8, 0.40)])

    unauthorised = [e for e in [
        ev("remote_access", "remote-access/screen-share session active", 0.70, t.remote_access_active),
        ev("new_binding", "UPI bound to this device within 24h", 0.50, t.device_bound_hours is not None and t.device_bound_hours < 24),
        ev("binding_old_device_live", "new binding while the old device is still online", 0.30, t.old_device_active and t.device_bound_hours is not None and t.device_bound_hours < 24),
        ev("sim_change", "SIM changed/ported within 72h", 0.45, t.sim_changed_hours is not None and t.sim_changed_hours < 72),
        ev("sideload", "app sideloaded within 48h", 0.30, t.recent_sideload_hours is not None and t.recent_sideload_hours < 48),
        ev("new_payee", "first payment to this payee", 0.15, new_payee),
        amount_ev,
        ev("odd_hour_new_payee", "unusual hour for this user, new payee", 0.20, odd_hour and new_payee),
    ] if e]

    coached = [e for e in [
        ev("collect_from_stranger", "collect request from a non-contact who is a new payee", 0.55,
           t.channel == "collect" and not t.requester_is_contact and new_payee),
        ev("call_active", "payment during a phone/video call", 0.30, t.call_active),
        ev("call_unknown", "the call is with an unsaved number", 0.15, t.call_active and t.call_unknown_number),
        ev("call_long", "call has run 20+ minutes", 0.20, t.call_active and t.call_minutes >= 20),
        ev("pay_stranger_on_call", "paying a new payee while on a call", 0.30, t.call_active and new_payee and ratio >= 1.5),
        ev("remote_access", "remote-access/screen-share session active", 0.50, t.remote_access_active),
        ev("new_payee", "first payment to this payee", 0.15, new_payee),
        amount_ev,
        tiered("drain", "large share of balance leaving", drain, [(0.5, 0.25), (0.8, 0.40)]),
        ev("pasted_amount", "amount pasted/dictated rather than typed", 0.10, t.pasted_amount),
        tiered("new_payee_streak", "several new payees paid in 72h", seq, [(2, 0.15), (3, 0.50)]),
        ev("escalating", "rising payments to a recently-added payee", 0.35, escalating),
        ev("risky_category", "payee category common in investment/task scams", 0.20,
           new_payee and t.payee_category in ("crypto", "gaming")),
    ] if e]

    payee = [e for e in [
        {"medium": ("fri", "payee number flagged FRI medium", 0.40),
         "high": ("fri", "payee number flagged FRI high", 0.75),
         "very_high": ("fri", "payee number flagged FRI very high", 0.95)}.get(t.payee_fri),
        ev("mule_score", "mule-account model score", 0.9 * (t.mule_score or 0), t.mule_score is not None and t.mule_score > 0),
        ev("payee_new_to_platform", "payee account is new to the platform", 0.20, t.payee_age_days is None or t.payee_age_days <= 7),
        tiered("payee_fan_in", "many unrelated payers sent to this payee in 24h", t.payee_distinct_payers_24h, [(10, 0.40), (25, 0.60)]),
        tiered("payee_complaints", "complaints against this payee", t.payee_complaints, [(1, 0.40), (3, 0.70)]),
    ] if e]

    groups = {UNAUTHORISED: unauthorised, COACHED: coached, PAYEE_RISK: payee}
    class_scores = {k: round(_noisy_or(v), 3) if v else 0.0 for k, v in groups.items()}
    threat = max(class_scores, key=class_scores.get)
    score = class_scores[threat]
    severity = _level(score)
    if severity == LOW:
        return Decision(NONE, LOW, score, "allow", [], class_scores)

    reasons = [(c, txt) for c, txt, w in sorted(groups[threat], key=lambda e: -e[2])]
    action = ACTIONS[threat][severity]
    return Decision(threat, severity, score, action, reasons, class_scores,
                    HOLD_MINUTES if action in ("cooling_off_hold", "hold_and_verify_payee") else 0)


# What the client is told. Deliberately does not reveal which signal fired for
# takeover-type decisions (an attacker could tune against it); coached-scam
# messages are educational, so they say what the pattern is.
_MESSAGES = {
    "device_biometric_reprompt": "For your security, please confirm it's you.",
    "face_challenge": "For your security, we need to verify it's you before this payment.",
    "block_and_lock_binding": "This payment was stopped to protect your account. Contact your bank.",
    "scam_warning": "Be careful: scammers often ask for payment during a call or chat. Banks and government agencies never do.",
    "cooling_off_hold": "This payment looks like a common scam pattern. It is on hold for {m} minutes; you can cancel it. Real agencies never ask you to pay during a call.",
    "block_and_offer_report": "We stopped this payment because it matches a known scam. If someone is pressuring you, hang up and call 1930.",
    "payee_risk_badge": "This payee has been reported before. Make sure you know and trust them.",
    "hold_and_verify_payee": "This payee looks risky. The payment is on hold for {m} minutes while we verify.",
    "block": "This payee is flagged as high risk for fraud, so the payment was blocked.",
}


def user_message(d: Decision) -> str:
    if d.action == "allow":
        return ""
    if d.action == "scam_warning" and any(c == "collect_from_stranger" for c, _ in d.reasons):
        return "This request will SEND money from your account. You never enter a PIN to receive money."
    return _MESSAGES[d.action].format(m=d.hold_minutes)
