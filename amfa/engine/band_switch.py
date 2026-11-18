"""The band switch, as the decision engine sees it (docs/rbi-banded-data-collection.md).

For every payment the engine works out, in the same coarse terms for both, what the rules would have asked for and what the
model would have asked for, plus a few categories about the payment. That view is stored on the plan and becomes the payment's
anonymous learning row when it settles (amfa/core/learning_store.py). Nothing here changes a decision.

Levels are compared on one scale for both sides:
  T0 a code only, T1 a code and typing, T2 adds one biometric (face or voice), T3 adds both, T4 held for a person or the
  model's top level. "Caught" for grading is T2 or above.
"""
from __future__ import annotations

from typing import Optional

from .context import Band, Context, Factor, Plan, TxnType
from .risk.collection_bands import band_of
from .risk.threat import REVIEW_LEVEL, hard_signals
from .rules import select_by_rules

MONEY = {TxnType.TRANSFER, TxnType.PAYMENT, TxnType.CARD_NOT_PRESENT}
LEVELS = ("T0", "T1", "T2", "T3", "T4")

# what the rules call low, medium and high value, by collection band: the same split as the bank's default bands (Rs 10,000 and
# Rs 1 lakh), so the rules ask exactly what they would for a bank on its defaults
RULES_BAND = {0: Band.LOW, 1: Band.LOW, 2: Band.MEDIUM, 3: Band.HIGH}


def level_of(required: list[Factor], held: bool) -> str:
    """A list of checks on the T0-T4 scale (the inverse of what each threat level asks for)."""
    if held:
        return "T4"
    bio = {Factor.FACE, Factor.VOICE} & set(required)
    if len(bio) == 2:
        return "T3"
    if bio:
        return "T2"
    return "T1" if Factor.UEBA in required else "T0"


def rules_plan(ctx: Context, band: int) -> Plan:
    """The rules-only plan, with the payment's collection band as its amount band."""
    t = ctx.transaction.model_copy(update={"amountBand": RULES_BAND[band]})
    return select_by_rules(ctx.model_copy(update={"transaction": t}))


def model_level(plan: Plan) -> str:
    """What the model alone asked for, before floors: the shadow level while the customer is still learning, the threat level
    otherwise. A model that could not score is the top level, as it would be if it were deciding (it fails closed)."""
    if plan.source == "learning":
        return plan.shadowLevel if plan.modelLevel is not None and plan.shadowLevel in LEVELS else REVIEW_LEVEL
    lvl = plan.threatLevel if plan.threatLevel in LEVELS else REVIEW_LEVEL
    return REVIEW_LEVEL if plan.escalate == "MANUAL_REVIEW" else lvl


BAND_ORDER = {Band.LOW: 0, Band.MEDIUM: 1, Band.HIGH: 2}
COLLECTING, ELIGIBLE, MODEL = "COLLECTING", "ELIGIBLE", "MODEL"


def state_of(ctx: Context) -> Optional[str]:
    """The band's state for this request: None when the switch is off or it is not a payment. Anything unknown is COLLECTING."""
    if not ctx.policy.bandSwitch or ctx.transaction.type not in MONEY:
        return None
    return ctx.policy.bandState if ctx.policy.bandState in (COLLECTING, ELIGIBLE, MODEL) else COLLECTING


def effective_state(ctx: Context, plan: Plan) -> Optional[str]:
    """The band's state for this payment once the model that scored it is known: MODEL only if that model's version is the one the
    band was approved on. A different, unknown or missing version means the rules set the minimum (COLLECTING)."""
    state = state_of(ctx)
    if state != MODEL:
        return state
    approved = ctx.policy.bandModelVersion
    return MODEL if approved and model_version(plan) == approved else COLLECTING


def apply(plan: Plan, ctx: Context) -> Plan:
    """Until a band is on the model, the rules set the minimum: the plan keeps every check the model asked for and gains every check
    the rules ask for that it lacks, and its risk band is the higher of the two. Nothing is ever removed, so this can only add
    friction. A customer still in the learning phase keeps the learning plan (already fixed, light checks with limits)."""
    state = effective_state(ctx, plan)
    plan.bandState = state
    if state is None or state == MODEL or plan.source == "learning":
        return plan
    rules = rules_plan(ctx, band_of(ctx.transaction.exposureMinor))
    added = [f for f in rules.required if f not in plan.required]
    if added:
        plan.required = list(plan.required) + added
        if plan.order:
            plan.order = list(plan.order) + [f for f in added if f not in plan.order]
        plan.bandRulesAdded = added
    if BAND_ORDER[rules.riskBand] > BAND_ORDER[plan.riskBand]:
        plan.riskBand = rules.riskBand
    return plan


def collecting(ctx: Context, plan: Plan) -> bool:
    """Is this payment decided with the rules as the minimum (the band is not on the model, or not on this model)?"""
    state = plan.bandState if plan.bandState is not None else effective_state(ctx, plan)
    return state is not None and state != MODEL and plan.source != "learning"


def merge(plan: Plan, model_plan: Plan) -> Plan:
    """The final plan while collecting: [plan] (the model's plan with the rules added, after floors) plus anything the model's own
    final plan has that it lacks. Checks are only added, the risk band is the higher, and a hold on either side is kept, so the
    result is at least as strong as the model's plan in every respect."""
    extra = [f for f in model_plan.required if f not in plan.required]
    if extra:
        plan.required = list(plan.required) + extra
        if plan.order:
            plan.order = list(plan.order) + [f for f in extra if f not in plan.order]
    if BAND_ORDER[model_plan.riskBand] > BAND_ORDER[plan.riskBand]:
        plan.riskBand = model_plan.riskBand
    if model_plan.escalate and not plan.escalate:
        plan.escalate, plan.escalateKind, plan.escalateReason = (model_plan.escalate, model_plan.escalateKind,
                                                                 model_plan.escalateReason)
    plan.scamRisk = plan.scamRisk or model_plan.scamRisk
    return plan


def model_version(plan: Plan) -> Optional[str]:
    """The risk model version that gave the plan's model level; None when no model scored (a failed model, a missing feature)."""
    if plan.source == "learning" and plan.modelLevel is None:
        return None
    if plan.modelError:
        return None
    from .risk.scorer import VERSION_NAME
    v = plan.modelVersion
    return v if isinstance(v, str) and VERSION_NAME.match(v) else None


def view(ctx: Context, plan: Plan, features: Optional[dict]) -> Optional[dict]:
    """The coarse view of a payment for the learning row, or None for anything that is not a payment. Called before floors and
    the language model change the plan, so it records the model's own call."""
    if ctx.transaction.type not in MONEY:
        return None
    f = features or {}
    band = band_of(ctx.transaction.exposureMinor)          # this payment plus the last 24 h; unknown is the top band
    rules = rules_plan(ctx, band)
    if f.get("payee_risk_flag") == 1:
        payee = "FLAGGED"
    else:
        payee = "NEW" if ctx.transaction.isNewBeneficiary else "KNOWN"
    if f.get("helper_device") == 1:
        device = "HELPER"
    else:
        device = "KNOWN" if ctx.device.deviceKnown else "NEW"
    return {"band": band,
            "rulesLevel": level_of(rules.required, rules.escalate == "MANUAL_REVIEW"),
            "modelLevel": model_level(plan),
            "payeeKind": payee, "deviceKind": device,
            "hardSign": bool(hard_signals(f)),
            "mode": effective_state(ctx, plan) or COLLECTING,
            "modelVersion": model_version(plan)}
