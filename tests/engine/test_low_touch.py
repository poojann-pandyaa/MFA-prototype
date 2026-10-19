"""Low-touch decisions, on the rule table: the strongest automated checks replace a person unless a hard-hold sign is present.
Admin is for: a payee on a risk list, an account takeover followed by a cash-out, repeated fake-face and
failed-check history, or a request whose regulatory floor can't be met with the checks available."""
import pytest

from amfa.engine.context import (ActionFloor, Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, OrgCtx,
                                 Policy, Segment, TransactionCtx, TxnType)
from amfa.engine.decide import decide
from amfa.engine.rationale import customer_notice

CALM = {"amount_log": 8.0, "amount_ratio": 1.0, "payee_seen": 0, "device_seen": 12, "payee_age_days": None,
        "history_count": 40,
        "amount_vs_payee_avg": None, "spoof_7d": 0, "failed_factors_24h": 0, "payee_risk_flag": 0,
        "sim_changed_hours": None, "contact_changed_hours": None, "password_reset_hours": None}


def ctx(*, type=TxnType.TRANSFER, new=True, exposure=Band.MEDIUM, amount=Band.MEDIUM, enrolled=None, low_touch=True,
        never_review=False, behaviour=Band.LOW):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=amount, exposureBand=exposure, isNewBeneficiary=new, type=type),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(band=behaviour),
                   enrollment=Enrollment(**(enrolled or {})), policy=Policy(lowTouch=low_touch),
                   org=OrgCtx(neverReview=never_review))


HIGH_RISK = dict(behaviour=Band.HIGH)          # the rule table's compound-risk row: every factor


def test_high_risk_gets_every_automated_check_and_no_person():
    plan = decide(ctx(**HIGH_RISK), features=CALM)
    assert plan.source.startswith("rules") and plan.riskBand is Band.HIGH and plan.escalate is None
    assert set(plan.required) == {Factor.OTP, Factor.UEBA, Factor.FACE, Factor.VOICE}


def test_a_missing_check_no_longer_sends_a_high_risk_request_to_a_person():
    missing = {"voice": False, "keystroke": False, "shape": False}
    plan = decide(ctx(enrolled=missing, **HIGH_RISK), features=CALM)
    assert plan.escalate is None and plan.floorMet
    assert Factor.FACE in plan.required and Factor.VOICE not in plan.required
    old = decide(ctx(enrolled=missing, low_touch=False, **HIGH_RISK), features=CALM)
    assert old.escalate == "MANUAL_REVIEW"


def test_if_the_regulatory_floor_cannot_be_met_a_person_still_looks():
    plan = decide(ctx(enrolled={"face": False, "voice": False, "keystroke": False, "shape": False}), features=CALM)
    assert not plan.floorMet and plan.escalate == "MANUAL_REVIEW" and "policy floor" in plan.escalateReason


def test_a_banks_higher_preference_gives_way_to_what_the_customer_has():
    """The bank prefers 3 checks for this action, the customer has 2 set up: use both, don't queue it."""
    two_only = {"voice": False, "keystroke": False, "shape": False}
    plan = decide(ctx(enrolled=two_only).model_copy(update={"policy": Policy(actionFloor=ActionFloor.L3)}),
                  features=CALM)
    assert plan.escalate is None and set(plan.required) == {Factor.OTP, Factor.FACE}
    assert "regulatory" not in (plan.escalateReason or "") and "all 2" in plan.autoHandled
    strict = decide(ctx(enrolled=two_only, low_touch=False).model_copy(
        update={"policy": Policy(actionFloor=ActionFloor.L3, lowTouch=False)}), features=CALM)
    assert strict.escalate == "MANUAL_REVIEW"


def test_a_watchlisted_customer_always_goes_to_a_person():
    watched = ctx(new=False, exposure=Band.LOW, amount=Band.LOW).model_copy(
        update={"org": OrgCtx(segment=Segment.WATCHLIST)})
    plan = decide(watched, features=CALM)
    assert plan.escalate == "MANUAL_REVIEW" and "watchlist" in plan.escalateReason


@pytest.mark.parametrize("risk", [dict(new=False, exposure=Band.LOW, amount=Band.LOW), dict(), HIGH_RISK])
def test_a_payee_on_a_risk_list_always_goes_to_a_person(risk):
    plan = decide(ctx(**risk), features=CALM | {"payee_risk_flag": 1})
    assert plan.escalate == "MANUAL_REVIEW" and "risk list" in plan.escalateReason


def test_a_hold_beats_the_never_review_waiver():
    plan = decide(ctx(type=TxnType.ADD_BENEFICIARY, never_review=True), features=CALM | {"payee_risk_flag": 1})
    assert plan.escalate == "MANUAL_REVIEW"


@pytest.mark.parametrize("change", [{"sim_changed_hours": 2.0}, {"contact_changed_hours": 1.0},
                                    {"password_reset_hours": 5.0}])
def test_account_takeover_followed_by_a_cash_out_goes_to_a_person(change):
    plan = decide(ctx(new=True, exposure=Band.MEDIUM), features=CALM | change)
    assert plan.escalate == "MANUAL_REVIEW" and "changed" in plan.escalateReason


def test_the_same_change_is_fine_for_a_known_payee_or_a_small_amount():
    sim = CALM | {"sim_changed_hours": 2.0}
    assert decide(ctx(new=False, exposure=Band.MEDIUM), features=sim).escalate is None
    assert decide(ctx(new=True, exposure=Band.LOW, amount=Band.LOW), features=sim).escalate is None


def test_repeated_fake_face_flags_with_failed_checks_go_to_a_person():
    plan = decide(ctx(), features=CALM | {"spoof_7d": 1, "failed_factors_24h": 2})
    assert plan.escalate == "MANUAL_REVIEW"
    assert decide(ctx(), features=CALM | {"spoof_7d": 1}).escalate is None     # one alone only adds checks


def test_a_very_large_first_payment_asks_for_voice_as_well_as_face():
    plan = decide(ctx(new=True, exposure=Band.HIGH, amount=Band.HIGH), features=CALM)
    assert {Factor.FACE, Factor.VOICE} <= set(plan.required) and plan.escalate is None


def test_without_voice_set_up_the_large_payment_still_goes_through():
    plan = decide(ctx(new=True, exposure=Band.HIGH, amount=Band.HIGH, enrolled={"voice": False}), features=CALM)
    assert Factor.VOICE not in plan.required and Factor.FACE in plan.required and plan.escalate is None


def test_a_large_payment_to_a_known_payee_does_not_force_voice():
    plan = decide(ctx(new=False, exposure=Band.HIGH, amount=Band.HIGH), features=CALM | {"payee_seen": 9})
    assert Factor.VOICE not in plan.required


def test_the_customer_notice_explains_without_revealing_detection():
    plan = decide(ctx(new=True), features=CALM)
    notice = customer_notice(plan, TxnType.TRANSFER, ctx(new=True))
    assert "this payee has not been paid before" in notice["reason"] and notice["scamWarning"]
    calm = decide(ctx(new=False, exposure=Band.LOW, amount=Band.LOW), features=CALM)
    assert customer_notice(calm, TxnType.TRANSFER, ctx(new=False, exposure=Band.LOW, amount=Band.LOW)) == {
        "reason": "This is a routine check to keep your account safe.", "scamWarning": False, "underReview": False}


class AlwaysAddsFace:
    """A stand-in AI helper that asks for a face check whenever it is consulted."""
    name = "fake"
    consulted = 0

    def system_prompt(self):
        return "system"

    def render(self, ctx, baseline):
        return "prompt"

    def propose(self, system, prompt):
        type(self).consulted += 1
        return '{"factors": ["FACE"], "reasoning": "looks new"}'


def test_the_ai_helper_is_not_consulted_when_the_risk_is_already_low():
    """It can only add friction, so at a low level there is nothing for it to improve."""
    AlwaysAddsFace.consulted = 0
    low = decide(ctx(new=False, exposure=Band.LOW, amount=Band.LOW), agent=AlwaysAddsFace(), features=CALM)
    assert low.riskBand is Band.LOW and AlwaysAddsFace.consulted == 0 and Factor.FACE not in low.required
    mid = decide(ctx(new=False, behaviour=Band.MEDIUM), agent=AlwaysAddsFace(), features=CALM)
    assert AlwaysAddsFace.consulted == 1 and Factor.FACE in mid.required and Factor.FACE in mid.agentAdded
    AlwaysAddsFace.consulted = 0                        # with low-touch off the helper is consulted as before
    old = decide(ctx(new=False, exposure=Band.LOW, amount=Band.LOW, low_touch=False), agent=AlwaysAddsFace(),
                 features=CALM)
    assert AlwaysAddsFace.consulted == 1 and Factor.FACE in old.required
