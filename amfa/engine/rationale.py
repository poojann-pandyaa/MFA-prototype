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


def reasoning(ctx: Context, plan: Plan) -> str:
    signals = present(ctx)
    parts = ["; ".join(signals[:4]) if signals else "no risk signal present"]
    org = org_present(ctx)
    if org:
        parts.append("context: " + ", ".join(org))
    if plan.orgAdded:
        parts.append("bank policy mandates " + ", ".join(f.value for f in plan.orgAdded))
    if plan.agentAdded:
        parts.append("model added " + ", ".join(f.value for f in plan.agentAdded)
                     + " beyond the rule baseline")
    if plan.agentCapped:
        parts.append("bank's friction budget withheld " + ", ".join(f.value for f in plan.agentCapped))
    if plan.policyFloorApplied:
        parts.append(f"raised to meet policy floor {plan.floor.value}")
    if plan.substituted:
        parts.append("substituted " + ", ".join(f"{k}->{v}" for k, v in plan.substituted.items()))
    if plan.escalate:
        parts.append(f"escalated to {plan.escalate.replace('_', ' ').lower()}")
    if not signals and not (plan.orgAdded or plan.agentAdded or plan.policyFloorApplied):
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
