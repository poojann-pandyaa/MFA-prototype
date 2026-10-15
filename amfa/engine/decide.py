from __future__ import annotations

import time
import uuid
from typing import Optional

from .agent import AgentClient, select_with_agent
from .availability import apply_availability, apply_org_requirements, enforce_floor
from .context import SELECTABLE, Band, Context, Plan
from .rationale import counterfactual, reasoning
from .risk import scorer
from .risk.threat import BAND, RESPONSE, REVIEW_LEVEL, small_payment_cap
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
                    threatLevel=REVIEW_LEVEL, modelError=err, escalate="MANUAL_REVIEW",
                    escalateReason=f"risk model unavailable ({err})")
    lvl, why = r.level, None
    if ctx.policy.smallPaymentCap and ctx.transaction.exposureBand is Band.LOW:
        lvl, why = small_payment_cap(r.level, features)
    plan = Plan(required=list(RESPONSE[lvl]), riskBand=BAND[lvl], source="model", riskScore=r.score,
                threatLevel=lvl, modelLevel=r.level, capReason=why, riskReasons=r.reasons, modelVersion=r.version)
    if lvl == REVIEW_LEVEL:
        plan.escalate, plan.escalateReason = "MANUAL_REVIEW", f"threat level {lvl} (risk {r.score:.2f})"
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

    if agent is not None:
        source = plan.source
        plan = select_with_agent(ctx, plan, agent)
        missing = [f for f in committed if f not in plan.required]
        if missing:
            raise MonotonicityViolation(
                f"agent dropped {[f.value for f in missing]} from the committed plan")
        plan.source = f"{source}+agent" if plan.agentAdded else source

    # the regulatory minimum (two factors, one dynamic) still applies after the model
    plan = apply_availability(plan, ctx)
    plan = enforce_floor(plan, ctx)

    plan.reasoning = reasoning(ctx, plan)
    plan.counterfactual = counterfactual(ctx)
    plan.planId = f"plan_{uuid.uuid4().hex[:12]}"
    plan.latencyMs = int((time.perf_counter() - started) * 1000)
    return plan
