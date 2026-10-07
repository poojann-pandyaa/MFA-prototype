from __future__ import annotations

from .context import Context, Factor, Plan

SYSTEM = """You select authentication factors for a banking transaction.

Available factors: FACE, VOICE, UEBA. OTP is always required and is not your decision.

Guidance:
- Select the fewest factors that address the risks actually present.
- Prefer UEBA when only behaviour is mildly unusual; it is the lowest-friction challenge.
- Prefer FACE when device or location is unfamiliar; it is the strongest proof of presence.
- Add VOICE when two independent biometrics are warranted.
- Select all three only when several independent risks compound.
- Respect the organisation's constraints: never propose a factor it does not permit.

You may only ADD assurance. A deterministic rule table has already produced a
baseline; anything you omit is still enforced. You cannot weaken the outcome."""


def render_prompt(ctx: Context, baseline: Plan) -> str:
    c, t, b, o = ctx.device, ctx.transaction, ctx.behavioural, ctx.org
    lines = [
        "Context:",
        f"  amount_band:        {t.amountBand.value}",
        f"  transaction_type:   {t.type.value}",
        f"  is_new_beneficiary: {str(t.isNewBeneficiary).lower()}",
        f"  device_known:       {str(c.deviceKnown).lower()}",
        f"  location_known:     {str(c.locationKnown).lower()}",
        f"  hour_unusual:       {str(c.hourUnusual).lower()}",
        f"  channel:            {c.channel}",
        f"  behavioural_band:   {b.band.value}",
        f"  behavioural_status: {b.status}",
        f"  dormant_account:    {str(b.dormant).lower()}",
        "",
        "Organisation:",
        f"  regulatory_profile: {o.regulatory.value}",
        f"  risk_appetite:      {o.appetite.value}",
        f"  customer_segment:   {o.segment.value}",
        f"  permitted_factors:  {', '.join(f.value for f in o.permitted)}",
        f"  mandated_factors:   {', '.join(f.value for f in o.required) or 'none'}",
        f"  action_floor:       {ctx.policy.actionFloor.value}",
        "",
        "Enrolled:   " + _flags(face=ctx.enrollment.face, voice=ctx.enrollment.voice,
                                keystroke=ctx.enrollment.keystroke, shape=ctx.enrollment.shape),
        "Capability: " + _flags(camera=ctx.capability.camera, microphone=ctx.capability.microphone,
                                pointer=ctx.capability.pointer),
        "",
        "Rule baseline already required: " + (", ".join(
            f.value for f in baseline.required if f is not Factor.OTP) or "nothing beyond OTP"),
    ]
    if ctx.policy.guidance:
        lines += ["", "Additional standing policy from the operator:", f"  {ctx.policy.guidance}"]
    lines += ["", "Return JSON only, no prose:",
              '{"factors": ["FACE"], "order": ["OTP","FACE"], '
              '"reasoning": "one short sentence", "counterfactual": "one short sentence"}']
    return "\n".join(lines)


def _flags(**kw) -> str:
    return ", ".join(f"{k}={str(v).lower()}" for k, v in kw.items())
