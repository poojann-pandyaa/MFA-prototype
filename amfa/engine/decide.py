from __future__ import annotations

import time
import uuid
from typing import Optional

from . import adjudicate
from .agent import AgentClient, select_with_agent
from .availability import apply_availability, apply_org_requirements, enforce_floor
from .context import SELECTABLE, Band, Context, Plan, dynamic_factors
from .rationale import counterfactual, reasoning
from .risk import scorer
from .risk.threat import (BAND, COLD_START_PAYMENTS, RESPONSE, REVIEW_LEVEL, new_customer_relief,
                          regular_payee_relief, small_payment_cap)
from .rules import select_by_rules


class MonotonicityViolation(AssertionError):
    pass


def select_by_model(features: Optional[dict], ctx: Context) -> Plan:
    """The risk model sets the threat level for every request. If it cannot score (no model, a
    model that fails its integrity check, missing features), fail closed to manual review. A payment
    in the bank's LOW amount band that is usual for the customer is held at the lightest extra check
    unless a hard warning sign is present."""
    try:
        if features is None:
            raise scorer.RiskModelError("no risk features for this request")
        r = scorer.active().score(features)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:200]
        return Plan(required=list(RESPONSE[REVIEW_LEVEL]), riskBand=BAND[REVIEW_LEVEL], source="model",
                    threatLevel=REVIEW_LEVEL, modelError=err, escalate="MANUAL_REVIEW", escalateKind="MODEL",
                    escalateReason=f"risk model unavailable ({err})")
    lvl, why = r.level, None
    exposure = ctx.transaction.exposureBand
    if ctx.policy.smallPaymentCap and exposure is Band.LOW:
        lvl, why = small_payment_cap(r.level, features)
    elif (ctx.policy.smallPaymentCap and ctx.policy.lowTouch and exposure is Band.MEDIUM
          and not ctx.transaction.isNewBeneficiary):
        lvl, why = regular_payee_relief(r.level, features)
    if lvl == r.level and ctx.policy.smallPaymentCap and ctx.policy.lowTouch and exposure in (Band.LOW, Band.MEDIUM):
        lvl, why = new_customer_relief(lvl, features)
    plan = Plan(required=list(RESPONSE[lvl]), riskBand=BAND[lvl], source="model", riskScore=r.score,
                threatLevel=lvl, modelLevel=r.level, capReason=why, riskReasons=r.reasons, modelVersion=r.version)
    if lvl == REVIEW_LEVEL:
        plan.escalate, plan.escalateKind = "MANUAL_REVIEW", "LEVEL"
        plan.escalateReason = f"threat level {lvl} (risk {r.score:.2f})"
    return plan


def decide(ctx: Context, agent: Optional[AgentClient] = None, features: Optional[dict] = None) -> Plan:
    started = time.perf_counter()

    plan = select_by_model(features, ctx)
    plan.rulesBaseline = select_by_rules(ctx).required  # logged for comparison only; never decides
    plan = apply_org_requirements(plan, ctx)
    committed = list(plan.required)

    from .availability import available
    addable = [f for f in SELECTABLE if f not in plan.required and available(f, ctx)]
    if agent is not None and (not addable or ctx.org.agentMaxAdditions == 0):
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
