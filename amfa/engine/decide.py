from __future__ import annotations

import time
import uuid
from typing import Optional

from . import adjudicate, band_switch
from .agent import AgentClient, select_with_agent
from .availability import apply_availability, apply_org_requirements, available, enforce_floor
from .context import SELECTABLE, Band, Context, Factor, Plan, dynamic_factors
from .rationale import counterfactual, reasoning
from .risk import scorer
from .risk.threat import (BAND, hard_signals, COLD_START_PAYMENTS, RESPONSE, REVIEW_LEVEL, customer_bypass, new_customer_relief,
                          raise_for_habit, regular_payee_relief, routine_relief, small_payment_cap)
from .rules import select_by_rules


class MonotonicityViolation(AssertionError):
    pass


def _score(features: Optional[dict]):
    if features is None:
        raise scorer.RiskModelError("no risk features for this request")
    return scorer.active().score(features)


def _relieved(r, features: dict, ctx: Context) -> tuple[str, Optional[str]]:
    """The level a customer would be asked for: the model's, lowered by the reliefs for ordinary payments, raised by their own habit."""
    lvl, why = r.level, None
    exposure = ctx.transaction.exposureBand
    if ctx.policy.smallPaymentCap and exposure is Band.LOW:
        lvl, why = small_payment_cap(r.level, features)
    elif (ctx.policy.smallPaymentCap and ctx.policy.lowTouch and exposure is Band.MEDIUM
          and not ctx.transaction.isNewBeneficiary):
        lvl, why = regular_payee_relief(r.level, features)
    elif (ctx.policy.smallPaymentCap and ctx.policy.lowTouch and exposure is Band.HIGH
          and not ctx.transaction.isNewBeneficiary):
        lvl, why = routine_relief(r.level, features)          # a weekly large payment is routine for some customers
    if lvl == r.level and ctx.policy.smallPaymentCap and ctx.policy.lowTouch and exposure in (Band.LOW, Band.MEDIUM):
        lvl, why = new_customer_relief(lvl, features)
    lvl, note = raise_for_habit(lvl, getattr(r, "habit", "T0"))
    return lvl, (note or why)


def select_by_model(features: Optional[dict], ctx: Context) -> Plan:
    """The risk model sets the threat level for every request. If it cannot score (no model, a
    model that fails its integrity check, missing features), fail closed to manual review. A payment
    in the bank's LOW amount band that is usual for the customer is held at the lightest extra check
    unless a hard warning sign is present."""
    try:
        r = _score(features)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:200]
        return Plan(required=list(RESPONSE[REVIEW_LEVEL]), riskBand=BAND[REVIEW_LEVEL], source="model",
                    threatLevel=REVIEW_LEVEL, modelError=err, escalate="MANUAL_REVIEW", escalateKind="MODEL",
                    escalateReason=f"risk model unavailable ({err})")
    lvl, why = _relieved(r, features, ctx)
    if ctx.policy.customerBypassMinor:
        lowered, note = customer_bypass(lvl, features, limit_minor=ctx.policy.customerBypassMinor,
                                        exposure_minor=ctx.transaction.exposureMinor,
                                        new_payee=ctx.transaction.isNewBeneficiary)
        if note:
            lvl, why = lowered, note
    plan = Plan(required=list(RESPONSE[lvl]), riskBand=BAND[lvl], source="model", riskScore=r.score,
                threatLevel=lvl, modelLevel=r.level, habitLevel=getattr(r, "habit", "T0"), capReason=why,
                riskReasons=r.reasons, modelVersion=r.version, phase="READY")
    if lvl == REVIEW_LEVEL:
        plan.escalate, plan.escalateKind = "MANUAL_REVIEW", "LEVEL"
        plan.escalateReason = f"threat level {lvl} (risk {r.score:.2f})"
    return plan


# the model must think a payment more likely fraud than not before it holds one while it is still learning the customer
LEARNING_HOLD_SCORE = 0.5


def learning_plan(features: Optional[dict], ctx: Context) -> Plan:
    """A customer the model does not know yet. The check is the phone's own fingerprint or screen lock (a signed challenge); a
    one-time code is added when the payment is beyond the learning limits or a hard warning sign is present. No face or voice here.
    The model still scores the payment, only to record what it would have asked for, and holds only when it is nearly sure."""
    r = None
    try:
        r = _score(features)
    except Exception:
        pass                              # learning does not depend on the model; the limits and warning signs carry it
    shadow, why = _relieved(r, features, ctx) if r is not None else ("T1", None)
    t = ctx.transaction
    over = []
    if ctx.policy.learningDailyMinor and (t.exposureMinor or 0) > ctx.policy.learningDailyMinor:
        over.append("more than the daily limit while we are still getting to know you")
    if ctx.policy.learningNewPayeeMinor and t.isNewBeneficiary and (t.exposureMinor or 0) > ctx.policy.learningNewPayeeMinor:
        over.append("a larger first payment to a new payee than the limit while we are still getting to know you")
    signs = hard_signals(features or {})
    first = Factor.DEVICE if available(Factor.DEVICE, ctx) else Factor.OTP
    stepped = bool(over or signs)
    required = [Factor.DEVICE, Factor.OTP] if (stepped and first is Factor.DEVICE) else [first]
    plan = Plan(required=required, riskBand=Band.MEDIUM if stepped else Band.LOW, source="learning", phase="LEARNING",
                threatLevel="T2" if stepped else "T1", shadowLevel=shadow, modelLevel=getattr(r, "level", None),
                habitLevel=getattr(r, "habit", None), riskScore=getattr(r, "score", None),
                riskReasons=getattr(r, "reasons", []), modelVersion=getattr(r, "version", ""),
                capReason="; ".join(over + signs) or "getting to know the customer: light checks")
    if r is not None and r.score >= LEARNING_HOLD_SCORE:
        plan.escalate, plan.escalateKind = "MANUAL_REVIEW", "HOLD"      # a hold, not a heavier check: the customer has none to give
        plan.escalateReason = f"the model is nearly sure this is fraud (risk {r.score:.2f})"
    return plan


def decide(ctx: Context, agent: Optional[AgentClient] = None, features: Optional[dict] = None) -> Plan:
    plan = _decide(ctx, agent, features)
    if band_switch.collecting(ctx, plan):
        # Until the band is on the model, the plan must hold everything the model's own final plan would: the regulatory filler,
        # the substitutes and the holds can differ once the rules have added checks, so take the model-only plan and add it in.
        model_ctx = ctx.model_copy(update={"policy": ctx.policy.model_copy(update={"bandState": band_switch.MODEL})})
        plan = band_switch.merge(plan, _decide(model_ctx, None, features))
    return plan


def _decide(ctx: Context, agent: Optional[AgentClient] = None, features: Optional[dict] = None) -> Plan:
    started = time.perf_counter()

    # The light plan needs the phone to sign (it is the whole check). A customer who cannot, such as one on a browser or on a phone
    # with no screen lock, would have only a code, which cannot meet the two-factor minimum: they keep the normal plan instead.
    learning = ctx.policy.phase == "LEARNING" and available(Factor.DEVICE, ctx)
    plan = learning_plan(features, ctx) if learning else select_by_model(features, ctx)
    plan.rulesBaseline = select_by_rules(ctx).required  # logged for comparison only; never decides
    plan.bandView = band_switch.view(ctx, plan, features)   # logged for the band switch's learning row, from the model's own call
    plan = band_switch.apply(plan, ctx)                     # until the band is on the model, the rules set the minimum
    plan = apply_org_requirements(plan, ctx)
    committed = list(plan.required)

    addable = [f for f in SELECTABLE if f not in plan.required and available(f, ctx)]
    if agent is not None and plan.source == "learning":
        plan.agentError = "skipped: learning phase, light checks"
        agent = None
    elif agent is not None and (not addable or ctx.org.agentMaxAdditions == 0):
        plan.agentError = "skipped: nothing the agent could add"
        agent = None
    elif agent is not None and ctx.policy.lowTouch and (
            plan.riskBand is Band.LOW or plan.capReason
            or (features or {}).get("history_count", 99) < COLD_START_PAYMENTS):
        # it can only add friction: not at a low level, not where a relief already judged the request ordinary,
        # and not for a customer with no history (everything about them looks "unfamiliar")
        plan.agentError = "skipped: low risk, judged ordinary or no history, and the helper can only add friction"
        agent = None

    if agent is not None:
        source = plan.source
        plan = select_with_agent(ctx, plan, agent)
        missing = [f for f in committed if f not in plan.required]
        if missing:
            raise MonotonicityViolation(
                f"agent dropped {[f.value for f in missing]} from the committed plan")
        plan.source = f"{source}+agent" if plan.agentAdded else source

    plan = adjudicate.shape(plan, ctx)
    # the regulatory minimum (two factors, one dynamic) still applies after the model
    plan = apply_availability(plan, ctx)
    plan = enforce_floor(plan, ctx)
    plan = adjudicate.resolve(plan, ctx, features)
    waived = None
    if plan.escalate == "MANUAL_REVIEW" and plan.escalateKind != "HOLD" and ctx.org.neverReview and \
            any(f in plan.required for f in dynamic_factors(ctx.org.permitted)):
        waived, plan.escalate, plan.escalateReason = plan.escalateReason, None, None

    plan.reasoning = reasoning(ctx, plan)
    if waived:
        plan.reasoning += f" Not held for review (this request type never waits for a person): {waived}."
    plan.counterfactual = counterfactual(ctx)
    plan.planId = f"plan_{uuid.uuid4().hex[:12]}"
    plan.latencyMs = int((time.perf_counter() - started) * 1000)
    return plan
