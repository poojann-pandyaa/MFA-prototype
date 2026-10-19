from __future__ import annotations

from bisect import bisect_right
from typing import Optional

from ..context import Band, Factor

LEVELS = ["T0", "T1", "T2", "T3", "T4"]

# Default friction: the share of genuine traffic expected at or above each level. Training turns
# these into score cut-offs on held-out genuine transactions.
GENUINE_SHARE_AT_OR_ABOVE = {"T1": .20, "T2": .05, "T3": .01, "T4": .002}

RESPONSE = {
    "T0": [Factor.OTP],
    "T1": [Factor.OTP, Factor.UEBA],
    "T2": [Factor.OTP, Factor.UEBA, Factor.FACE],
    "T3": [Factor.OTP, Factor.UEBA, Factor.FACE, Factor.VOICE],
    "T4": [Factor.OTP, Factor.UEBA, Factor.FACE, Factor.VOICE],
}
BAND = {"T0": Band.LOW, "T1": Band.LOW, "T2": Band.MEDIUM, "T3": Band.HIGH, "T4": Band.HIGH}
REVIEW_LEVEL = "T4"


def level(raw_score: float, cutoffs: list[float]) -> str:
    """cutoffs are the ascending raw scores at which T1..T4 begin."""
    return LEVELS[bisect_right(cutoffs, raw_score)]


# ---------- proportionate friction: small payments do not need the heavy checks ----------

CAP_LEVEL = "T1"       # OTP plus the passive behaviour check: still meets the two-factor minimum
USUAL_RATIO = 5.0      # "not unusually large for this customer": under 5x their usual payment


def hard_signals(f: dict) -> list[str]:
    """Warning signs that always keep the model's level, however small the payment."""
    def within(name: str, hours: float) -> bool:   # a missing value means it never happened
        return f.get(name) is not None and f[name] < hours

    checks = {
        "spoof attempt this week": (f.get("spoof_7d") or 0) > 0,
        "repeated failed checks": (f.get("failed_factors_24h") or 0) >= 2,
        "payee on a risk list": f.get("payee_risk_flag") == 1,
        "SIM changed within 48 h": within("sim_changed_hours", 48),
        "password reset within 24 h": within("password_reset_hours", 24),
        "contact details changed within 24 h": within("contact_changed_hours", 24),
        "account setting changed today": (f.get("sensitive_actions_24h") or 0) > 0,
        "three or more new payees today": (f.get("new_payees_24h") or 0) >= 3,
    }
    return [name for name, hit in checks.items() if hit]


REGULAR_MIN_PAYMENTS = 3       # a payee is "regular" after this many verified payments...
REGULAR_MIN_AGE_DAYS = 30      # ...over at least this long
REGULAR_USUAL_RATIO = 3.0      # and a payment is usual for it when under this multiple of what's normally paid


def regular_payee_relief(level: str, f: dict) -> tuple[str, Optional[str]]:
    """Mid-sized payments to a payee the customer has long paid, at about the usual amount, from a device
    they use, with no warning sign: kept at the light level. Same shape as small_payment_cap."""
    ratio = f.get("amount_vs_payee_avg")
    regular = ((f.get("payee_seen") or 0) >= REGULAR_MIN_PAYMENTS and (f.get("payee_age_days") or 0) >= REGULAR_MIN_AGE_DAYS
               and ratio is not None and ratio < REGULAR_USUAL_RATIO and (f.get("device_seen") or 0) > 0)
    if not regular or LEVELS.index(level) <= LEVELS.index(CAP_LEVEL) or hard_signals(f):
        return level, None
    return CAP_LEVEL, f"regular payee at the usual amount, kept at {CAP_LEVEL}"


COLD_START_PAYMENTS = 5        # fewer verified payments than this and the customer has no routine to deviate from
COLD_START_LEVEL = "T2"        # code + typing + face


def new_customer_relief(level: str, f: dict) -> tuple[str, Optional[str]]:
    """With almost no history, every payee, device and hour is unfamiliar, which says nothing about fraud.
    Payments (not account changes) from a clean record are held at COLD_START_LEVEL; the caller keeps very
    large amounts out of this. Same shape as small_payment_cap."""
    seen = f.get("history_count")     # unknown is not the same as none: no relief without knowing
    if (f.get("amount_log") is None or seen is None or seen >= COLD_START_PAYMENTS
            or LEVELS.index(level) <= LEVELS.index(COLD_START_LEVEL) or hard_signals(f)):
        return level, None
    return COLD_START_LEVEL, f"new customer with too little history to judge, kept at {COLD_START_LEVEL}"


def small_payment_cap(level: str, f: dict) -> tuple[str, Optional[str]]:
    """The level to use for a payment already known to be small (the caller checks the amount line).
    Returns (level, why it was lowered or None). Never raises a level, and never lowers one when a
    hard signal is present or the amount is unusual for this customer."""
    if f.get("amount_log") is None:   # not a payment (adding a payee, a limit or device change): never lenient
        return level, None
    if LEVELS.index(level) <= LEVELS.index(CAP_LEVEL) or hard_signals(f):
        return level, None
    if (f.get("amount_ratio") or 0) >= USUAL_RATIO:
        return level, None
    return CAP_LEVEL, f"small payment within this customer's usual, kept at {CAP_LEVEL}"
