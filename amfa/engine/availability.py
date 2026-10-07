from __future__ import annotations

from .context import (FLOOR_MINIMUM, FRICTION_ORDER, ActionFloor, Band, Context, Factor, Plan, dynamic_factors)

SUBSTITUTES = {
    Factor.FACE: [Factor.VOICE],
    Factor.VOICE: [Factor.FACE],
    Factor.UEBA: [Factor.FACE, Factor.VOICE],
}


def available(f: Factor, ctx: Context) -> bool:
    if f not in ctx.org.permitted:
        return False
    e, c = ctx.enrollment, ctx.capability
    if f is Factor.OTP:
        return e.otp
    if f is Factor.FACE:
        return e.face and c.camera
    if f is Factor.VOICE:
        return e.voice and c.microphone
    if f is Factor.UEBA:
        return e.keystroke
    return False


def apply_org_requirements(plan: Plan, ctx: Context) -> Plan:
    for f in ctx.org.required:
        if f not in plan.required:
            plan.required.append(f)
            plan.orgAdded.append(f)
    return plan


def apply_availability(plan: Plan, ctx: Context) -> Plan:
    resolved: list[Factor] = []
    unmet: list[Factor] = []
    for f in plan.required:
        if f is Factor.OTP and f not in ctx.org.permitted:
            continue
        if available(f, ctx):
            if f not in resolved:
                resolved.append(f)
            continue
        alt = next((a for a in SUBSTITUTES.get(f, []) if available(a, ctx)
                    and a not in resolved and a not in plan.required), None)
        if alt is not None:
            resolved.append(alt)
            plan.substituted[f.value] = alt.value
        else:
            unmet.append(f)

    plan.required = resolved
    plan.unmet = unmet
    if unmet and (plan.riskBand is Band.HIGH or ctx.org.reviewOnAnyUnmet):
        plan.escalate = "MANUAL_REVIEW"
        plan.escalateReason = "required factors cannot be assembled: " + \
            ", ".join(f.value for f in unmet)
    return plan


def enforce_floor(plan: Plan, ctx: Context) -> Plan:
    floor = ctx.policy.actionFloor
    plan.floor = floor
    before = len(plan.required)

    dynamic = dynamic_factors(ctx.org.permitted)
    if not any(f in plan.required for f in dynamic):
        pick = next((f for f in dynamic if available(f, ctx)), None)
        if pick is Factor.OTP:
            plan.required.insert(0, pick)
        elif pick is not None:
            plan.required.append(pick)

    candidates = [f for f in FRICTION_ORDER if f not in plan.required and available(f, ctx)]
    needed = FLOOR_MINIMUM[floor]
    if floor is ActionFloor.L4:
        needed = len(plan.required) + len(candidates)

    for f in candidates:
        if len(plan.required) >= needed:
            break
        plan.required.append(f)

    plan.policyFloorApplied = len(plan.required) > before
    plan.floorMet = len(plan.required) >= min(needed, 4) and any(f in plan.required for f in dynamic)
    if not plan.floorMet and plan.escalate is None:
        plan.escalate = "MANUAL_REVIEW"
        plan.escalateReason = (f"policy floor {floor.value} needs {needed} factors; only "
                               f"{len(plan.required)} can run on this device and enrolment")

    if not plan.order:
        plan.order = [f for f in FRICTION_ORDER if f in plan.required]
    else:
        plan.order = [f for f in plan.order if f in plan.required] + [
            f for f in FRICTION_ORDER if f in plan.required and f not in plan.order]
    if Factor.OTP in plan.order:
        plan.order.remove(Factor.OTP)
        plan.order.insert(0, Factor.OTP)
    return plan
