from __future__ import annotations

import time
import uuid
from typing import Optional

from .agent import AgentClient, select_with_agent
from .availability import apply_availability, apply_org_requirements, enforce_floor
from .context import SELECTABLE, Context, Plan
from .rationale import counterfactual, reasoning
from .rules import select_by_rules


class MonotonicityViolation(AssertionError):
    pass


def decide(ctx: Context, agent: Optional[AgentClient] = None) -> Plan:
    started = time.perf_counter()

    plan = select_by_rules(ctx)
    plan.rulesBaseline = list(plan.required)
    plan = apply_org_requirements(plan, ctx)
    committed = list(plan.required)

    from .availability import available
    addable = [f for f in SELECTABLE if f not in plan.required and available(f, ctx)]
    if agent is not None and (not addable or ctx.org.agentMaxAdditions == 0):
        plan.source = "rules"
        plan.agentError = "skipped: nothing the agent could add"
        agent = None

    if agent is not None:
        plan = select_with_agent(ctx, plan, agent)
        missing = [f for f in committed if f not in plan.required]
        if missing:
            raise MonotonicityViolation(
                f"agent dropped {[f.value for f in missing]} from the committed plan")
    else:
        plan.source = "rules"

    plan = apply_availability(plan, ctx)
    plan = enforce_floor(plan, ctx)

    plan.reasoning = reasoning(ctx, plan)
    plan.counterfactual = counterfactual(ctx)
    plan.planId = f"plan_{uuid.uuid4().hex[:12]}"
    plan.latencyMs = int((time.perf_counter() - started) * 1000)
    return plan


def rules_only(ctx: Context) -> Plan:
    return decide(ctx, agent=None)
