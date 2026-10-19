from __future__ import annotations

import time
import uuid
from typing import Optional

from . import adjudicate
from .agent import AgentClient, select_with_agent
from .availability import apply_availability, apply_org_requirements, enforce_floor
from .context import SELECTABLE, Band, Context, Plan, dynamic_factors
from .rationale import counterfactual, reasoning
from .rules import select_by_rules


class MonotonicityViolation(AssertionError):
    pass


def decide(ctx: Context, agent: Optional[AgentClient] = None, features: Optional[dict] = None) -> Plan:
    started = time.perf_counter()

    plan = select_by_rules(ctx)                        # the reviewed rule table sets the baseline
    plan.rulesBaseline = list(plan.required)
    plan = apply_org_requirements(plan, ctx)
    committed = list(plan.required)

    from .availability import available
    addable = [f for f in SELECTABLE if f not in plan.required and available(f, ctx)]
    if agent is not None and (not addable or ctx.org.agentMaxAdditions == 0):
        plan.agentError = "skipped: nothing the agent could add"
        agent = None
    elif agent is not None and ctx.policy.lowTouch and plan.riskBand is Band.LOW:
        # it can only add friction: at a low level there is nothing for it to improve
        plan.agentError = "skipped: low risk, and the helper can only add friction"
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
    # the regulatory minimum (two factors, one dynamic) is applied last
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


def rules_only(ctx: Context) -> Plan:
    return decide(ctx, agent=None)
