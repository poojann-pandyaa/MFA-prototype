from __future__ import annotations

from .context import Appetite, Band, Context, Segment, TxnType

SYSTEM = """You review an authentication plan for a bank transaction and catch what it missed.

A rule table produced the baseline. The table is deliberately simple and has known
blind spots: it reacts to a device change and a location change TOGETHER, but not
to a location change alone; it reacts to dormancy only on large amounts; it does
not combine several individually weak signals; and it ignores the organisation's
context -- the customer's segment, the transaction type and the bank's risk appetite.

Your job: for each risk signal that is present, decide whether the baseline already
answers it. If every present signal is answered, return an empty list. If a signal
is present and unanswered, add the cheapest factor that answers it.

  FACE   proves the enrolled person is physically present. Answers an unfamiliar
         device, an unfamiliar location, a new payee, or a reactivated account.
  VOICE  a second, independent biometric. Add only when one is not enough: both
         device and location unfamiliar, or several risks at once.
  UEBA   lowest friction, confirms the person by behaviour. Answers mildly unusual
         behaviour. Worthless once device or location changed: its baseline is stale.

Organisational context raises the bar, never lowers it: a CONSERVATIVE bank, a
PREMIUM, SENIOR or WATCHLIST customer, or a sensitive transaction type (limit
change, profile change, device binding, new beneficiary) justifies one more factor
where a risk signal is already present.

Never add a factor already in the baseline. Never add one the device cannot run or
the organisation does not permit. Return JSON only."""

SENSITIVE_TYPES = {TxnType.CHANGE_LIMIT, TxnType.PROFILE_CHANGE, TxnType.DEVICE_BINDING,
                   TxnType.ADD_BENEFICIARY}
ELEVATED_SEGMENTS = {Segment.PREMIUM, Segment.SENIOR, Segment.WATCHLIST}


def render(ctx: Context, baseline) -> str:
    d, t, b, o = ctx.device, ctx.transaction, ctx.behavioural, ctx.org
    signals = []
    if not d.deviceKnown:
        signals.append("device is unrecognised")
    if not d.locationKnown:
        signals.append("location is unfamiliar")
    if t.isNewBeneficiary:
        signals.append("payee was added recently")
    if b.band is not Band.LOW:
        signals.append(f"behavioural risk is {b.band.value}")
    if b.status == "INSUFFICIENT_DATA":
        signals.append("behavioural history is too thin to assess")
    if b.dormant:
        signals.append("account is reactivating after dormancy")
    if t.amountBand is Band.HIGH:
        signals.append("amount is high")
    if d.hourUnusual:
        signals.append("hour is unusual")

    org = []
    if t.type in SENSITIVE_TYPES:
        org.append(f"transaction type is {t.type.value}")
    if o.segment in ELEVATED_SEGMENTS:
        org.append(f"customer segment is {o.segment.value}")
    if o.appetite is Appetite.CONSERVATIVE:
        org.append("the bank's risk appetite is CONSERVATIVE")

    body = "\n".join(f"  - {s}" for s in signals) if signals else "  - none"
    permitted = [f.value for f in o.permitted if f.value != "OTP"]
    runnable = [f for f in permitted
                if (f != "FACE" or ctx.capability.camera) and (f != "VOICE" or ctx.capability.microphone)]
    out = [f"RISK SIGNALS PRESENT:\n{body}"]
    if org:
        out += ["", "ORGANISATIONAL CONTEXT:\n" + "\n".join(f"  - {s}" for s in org)]
    out += ["",
            f"BASELINE ALREADY REQUIRES: {', '.join(f.value for f in baseline.required)}",
            f"FACTORS YOU MAY ADD: {', '.join(runnable) or 'none'}"]
    if ctx.policy.guidance:
        out += ["", f"STANDING POLICY FROM COMPLIANCE: {ctx.policy.guidance}"]
    return "\n".join(out)


RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["factors"],
    "properties": {
        "factors": {
            "type": "array", "maxItems": 3,
            "items": {"enum": ["UEBA", "FACE", "VOICE"]},
            "description": "Factors to ADD to the baseline. Empty if the baseline is right.",
        },
        "order": {"type": "array", "maxItems": 4,
                  "items": {"enum": ["OTP", "UEBA", "FACE", "VOICE"]}},
    },
}
