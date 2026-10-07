from __future__ import annotations

from .context import Band, Context, Factor, Plan

HIGH_VALUE_BANDS = {Band.HIGH}


def select_by_rules(ctx: Context) -> Plan:
    t, d, b = ctx.transaction, ctx.device, ctx.behavioural

    high_value = t.amountBand in HIGH_VALUE_BANDS
    mid_value = t.amountBand is Band.MEDIUM
    new_payee = t.isNewBeneficiary
    new_device = not d.deviceKnown
    new_place = not d.locationKnown
    odd_hour = d.hourUnusual
    anomaly = b.band is Band.HIGH
    dormant = b.dormant

    factors: list[Factor] = [Factor.OTP]

    if anomaly or (high_value and new_payee and odd_hour):
        factors += [Factor.FACE, Factor.VOICE, Factor.UEBA]
        band, why = Band.HIGH, "compound risk across independent signals"

    elif new_device and new_place:
        factors += [Factor.FACE, Factor.VOICE]
        band, why = Band.HIGH, "unfamiliar device and unfamiliar location"

    elif dormant and high_value:
        factors += [Factor.FACE, Factor.VOICE]
        band, why = Band.HIGH, "high value on a reactivated dormant account"

    elif high_value or new_payee:
        factors += [Factor.FACE, Factor.UEBA]
        band, why = Band.MEDIUM, "new payee" if new_payee else "high transaction value"

    elif new_device:
        factors += [Factor.FACE]
        band, why = Band.MEDIUM, "unrecognised device"

    elif b.band is Band.MEDIUM or (mid_value and odd_hour):
        factors += [Factor.UEBA]
        band, why = Band.MEDIUM, "mildly unusual behaviour"

    else:
        band, why = Band.LOW, "trusted device, familiar payee, routine value"

    return Plan(
        required=_dedupe(factors),
        riskBand=band,
        source="rules",
        reasoning=why,
    )


def _dedupe(factors: list[Factor]) -> list[Factor]:
    seen, out = set(), []
    for f in factors:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out
