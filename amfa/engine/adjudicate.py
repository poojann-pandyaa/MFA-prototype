"""Low-touch decisions: who has to look at a request, if anyone.

The risk model and the policy floors decide which checks to run. This step decides whether a person is
needed on top of them. By default a high threat level, a check the customer hasn't set up, or a risk-model
outage is answered with the strongest checks that can run, not with a queue. A person is kept for what
automated checks can't settle:

  * a payee on a risk list;
  * an account takeover followed by a cash-out (SIM, contact details or password changed in the last day or
    two, then money to a new payee);
  * a recent suspected fake face together with repeated failed checks;
  * a request whose regulatory minimum can't be met with the checks the customer has (decided in
    availability.py and left alone here).

Biometrics prove the person at the camera is the customer. They can't tell that the customer is being
tricked into paying, so large first payments also carry the scam warning and a second biometric."""
from __future__ import annotations

from .availability import available
from .context import FLOOR_MINIMUM, Band, Context, Factor, Plan, Segment, TxnType, dynamic_factors
from .policy import REGULATORY

MONEY = {TxnType.TRANSFER, TxnType.PAYMENT, TxnType.CARD_NOT_PRESENT}
ACCOUNT_CHANGES = (("sim_changed_hours", "SIM", 48), ("contact_changed_hours", "contact details", 24),
                   ("password_reset_hours", "password", 24))
SOFT = {"LEVEL", "MODEL", "UNMET"}      # answered with stronger automated checks, never a person by themselves


def large_first_payment(ctx: Context) -> bool:
    t = ctx.transaction
    return t.type in MONEY and t.isNewBeneficiary and Band.HIGH in (t.exposureBand, t.amountBand)


def hold_reasons(ctx: Context, features: dict | None) -> list[str]:
    """Signs that need a person even when every check passes. Empty when nothing needs one."""
    f = features or {}
    out = []
    if f.get("payee_risk_flag") == 1:
        out.append("the payee is on a risk list")
    t = ctx.transaction
    if t.type in MONEY and t.isNewBeneficiary and t.exposureBand in (Band.MEDIUM, Band.HIGH):
        changed = [name for key, name, hours in ACCOUNT_CHANGES if f.get(key) is not None and f[key] < hours]
        if changed:
            out.append(f"{' and '.join(changed)} changed very recently, then money to a new payee")
    if (f.get("spoof_7d") or 0) > 0 and (f.get("failed_factors_24h") or 0) >= 2:
        out.append("a suspected fake-face attempt this week and repeated failed checks today")
    if ctx.org.segment is Segment.WATCHLIST:
        out.append("the customer is on the bank's watchlist")
    return out


def shape(plan: Plan, ctx: Context) -> Plan:
    """Before availability: an exceptionally large first payment gets a second biometric when one is set up."""
    if not ctx.policy.lowTouch:
        return plan
    plan.scamRisk = large_first_payment(ctx)
    if plan.scamRisk and Factor.VOICE not in plan.required and available(Factor.VOICE, ctx):
        plan.required.append(Factor.VOICE)
    return plan


def resolve(plan: Plan, ctx: Context, features: dict | None) -> Plan:
    """After the floor: keep a person only where one is needed."""
    if not ctx.policy.lowTouch:
        return plan
    holds = hold_reasons(ctx, features)
    if holds:
        plan.escalate, plan.escalateKind, plan.escalateReason = "MANUAL_REVIEW", "HOLD", "; ".join(holds)
        return plan
    if plan.escalate == "MANUAL_REVIEW" and plan.escalateKind in SOFT | {"FLOOR"}:
        # the regulation's minimum is what can't be given up; the bank's own higher preference bends to what
        # the customer has set up (every available check is already in the plan by now)
        have = len(plan.required)
        if have >= REGULATORY_MINIMUM(ctx) and any(f in plan.required for f in dynamic_factors(ctx.org.permitted)):
            plan.autoHandled = (f"{plan.escalateReason}: handled with the strongest automated checks "
                                f"instead of a person (all {have} the customer can do)")
            plan.floorMet = True
            plan.escalate = plan.escalateReason = plan.escalateKind = None
        else:
            plan.escalateKind = "FLOOR"
    return plan


def REGULATORY_MINIMUM(ctx: Context) -> int:
    return FLOOR_MINIMUM[REGULATORY[ctx.org.regulatory].min_floor]
