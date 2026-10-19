from __future__ import annotations

from .context import Appetite, Band, Context, Plan, Segment, TxnType

SIGNALS = [
    (lambda c: c.transaction.isNewBeneficiary, "payee added recently"),
    (lambda c: not c.device.deviceKnown, "unrecognised device"),
    (lambda c: not c.device.locationKnown, "unfamiliar location"),
    (lambda c: c.behavioural.band is Band.HIGH and c.behavioural.status == "OK",
     "behavioural risk high"),
    (lambda c: c.behavioural.status == "INSUFFICIENT_DATA", "behavioural history too thin"),
    (lambda c: c.behavioural.status == "UNAVAILABLE", "behavioural service unavailable"),
    (lambda c: c.behavioural.band is Band.MEDIUM, "behaviour mildly unusual"),
    (lambda c: c.behavioural.dormant, "account reactivated after dormancy"),
    (lambda c: c.transaction.amountBand is Band.HIGH, "high value"),
    (lambda c: c.device.hourUnusual, "unusual hour"),
]

ORG_SIGNALS = [
    (lambda c: c.transaction.type in (TxnType.CHANGE_LIMIT, TxnType.PROFILE_CHANGE,
                                      TxnType.DEVICE_BINDING, TxnType.ADD_BENEFICIARY),
     lambda c: f"sensitive action ({c.transaction.type.value.lower().replace('_', ' ')})"),
    (lambda c: c.org.segment is not Segment.STANDARD,
     lambda c: f"{c.org.segment.value.lower().replace('_', ' ')} customer"),
    (lambda c: c.org.appetite is Appetite.CONSERVATIVE, lambda c: "conservative bank policy"),
]


def present(ctx: Context) -> list[str]:
    return [phrase for pred, phrase in SIGNALS if pred(ctx)]


def org_present(ctx: Context) -> list[str]:
    return [phrase(ctx) for pred, phrase in ORG_SIGNALS if pred(ctx)]


REASON_TEXT = {
    "amount_ratio": "amount far above usual", "amount_vs_max": "largest amount so far",
    "amount_24h_ratio": "unusual total sent today", "amount_log": "transaction size",
    "velocity_1h": "many payments in the last hour", "velocity_24h": "many payments today",
    "new_payees_24h": "several new payees today", "payee_seen": "payee not paid before",
    "payee_age_days": "payee added recently", "device_seen": "device not seen before",
    "device_age_days": "device new to this customer", "network_seen": "unfamiliar network",
    "place_seen": "unfamiliar location", "place_changed": "location changed since last payment",
    "country_changed": "country changed since last payment", "minutes_since_last": "timing since last payment",
    "days_since_last_ok": "long gap since last verified payment", "hour_deviation": "unusual time of day",
    "is_night": "night-time payment", "failed_factors_24h": "recent failed verifications",
    "spoof_7d": "recent spoofing attempt", "history_count": "little payment history",
    "account_age_days": "account age", "enrolled_factors": "few enrolled factors",
    "ueba_risk": "behaviour unlike the customer", "sim_changed_hours": "SIM changed recently",
    "contact_changed_hours": "contact details changed recently",
    "password_reset_hours": "password reset recently", "payee_risk_flag": "payee on a risk list",
    "device_key_bound": "device not key-bound", "txn_type": "transaction type", "channel": "channel",
    "segment": "customer segment",
}


# What a customer may be told. Spoof, failure, behaviour and risk-list signals are left out so a
# fraudster learns nothing about what was detected; segment and account facts are not reasons to show.
CUSTOMER_REASONS = {
    "amount_ratio": "the amount is larger than usual", "amount_vs_max": "the amount is larger than usual",
    "amount_24h_ratio": "you have sent more than usual today", "amount_vs_payee_avg": "the amount is unusual for this payee",
    "velocity_1h": "several payments in a short time", "velocity_24h": "several payments today",
    "new_payees_24h": "new payees were added today", "payee_seen": "this payee has not been paid before",
    "payee_age_days": "this payee was added recently", "device_seen": "this device is new to your account",
    "device_age_days": "this device is new to your account", "network_seen": "you are on an unfamiliar network",
    "place_seen": "you are in an unfamiliar place", "place_changed": "your location has changed",
    "country_changed": "your location has changed", "hour_deviation": "this is an unusual time for you",
    "is_night": "this is an unusual time for you", "sim_changed_hours": "your SIM was changed recently",
    "contact_changed_hours": "your contact details were changed recently",
    "password_reset_hours": "your password was changed recently",
}
SCAM_WARNING_FROM = "T2"
MONEY_TYPES = {TxnType.TRANSFER, TxnType.PAYMENT, TxnType.CARD_NOT_PRESENT}


def customer_notice(plan: Plan, txn_type: TxnType) -> dict:
    """What the customer sees before the checks: a plain reason and, for riskier payments, a scam
    warning. Never the score, the threat level or the thresholds."""
    why = list(dict.fromkeys(CUSTOMER_REASONS[k] for k, _ in plan.riskReasons if k in CUSTOMER_REASONS))
    reason = ("We need a quick extra check because " + " and ".join(why[:2]) + "." if why
              else "This is a routine check to keep your account safe.")
    warn = txn_type in MONEY_TYPES and (plan.scamRisk or (plan.threatLevel is not None
                                                          and plan.threatLevel >= SCAM_WARNING_FROM))
    return {"reason": reason, "scamWarning": warn, "underReview": plan.escalate == "MANUAL_REVIEW"}


def model_summary(plan: Plan) -> str:
    if plan.modelError:
        return f"risk model unavailable, failed closed to {plan.threatLevel}"
    why = ", ".join(REASON_TEXT.get(k, k) for k, _ in plan.riskReasons)
    text = f"threat {plan.threatLevel} (risk {plan.riskScore:.2f})" + (f": {why}" if why else "")
    return text + (f". Model said {plan.modelLevel}; {plan.capReason}" if plan.capReason else "")


def reasoning(ctx: Context, plan: Plan) -> str:
    signals = present(ctx)
    parts = [model_summary(plan) if plan.threatLevel else
             "; ".join(signals[:4]) if signals else "no risk signal present"]
    org = org_present(ctx)
    if org:
        parts.append("context: " + ", ".join(org))
    if plan.orgAdded:
        parts.append("bank policy mandates " + ", ".join(f.value for f in plan.orgAdded))
    if plan.agentAdded:
        parts.append("model added " + ", ".join(f.value for f in plan.agentAdded)
                     + " beyond the risk model")
    if plan.agentCapped:
        parts.append("bank's friction budget withheld " + ", ".join(f.value for f in plan.agentCapped))
    if plan.policyFloorApplied:
        parts.append(f"raised to meet policy floor {plan.floor.value}")
    if plan.substituted:
        parts.append("substituted " + ", ".join(f"{k}->{v}" for k, v in plan.substituted.items()))
    if plan.scamRisk:
        parts.append("large first payment to a new payee: scam warning shown, second biometric asked where set up")
    if plan.autoHandled:
        parts.append(plan.autoHandled)
    if plan.escalate:
        parts.append(f"escalated to {plan.escalate.replace('_', ' ').lower()}")
    if not plan.threatLevel and not signals and not (plan.orgAdded or plan.agentAdded or plan.policyFloorApplied):
        return "no risk signal present; minimum assurance only"
    return ". ".join(parts)


def counterfactual(ctx: Context) -> str:
    if not ctx.device.deviceKnown:
        return "a recognised device would have reduced this to the behavioural challenge"
    if ctx.transaction.isNewBeneficiary:
        return "an established payee would have removed the biometric requirement"
    if ctx.behavioural.band is Band.HIGH and ctx.behavioural.status == "OK":
        return "a normal behavioural band would have left this at OTP alone"
    if ctx.behavioural.status == "INSUFFICIENT_DATA":
        return "an established behavioural history would have lowered the risk band"
    if not ctx.device.locationKnown:
        return "a familiar location would have left this at OTP alone"
    if ctx.transaction.amountBand is Band.HIGH:
        return "a lower amount band would have reduced the requirement"
    return "no single signal dominates this decision"
