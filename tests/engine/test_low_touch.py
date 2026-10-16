"""Low-touch decisions: the strongest automated checks replace a person unless a hard-hold sign is present.
Admin is for: a payee on a risk list, an account takeover followed by a cash-out, repeated fake-face and
failed-check history, or a request whose regulatory floor can't be met with the checks available."""
import pytest

from amfa.engine.context import (ActionFloor, Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, OrgCtx,
                                 Policy, Segment, TransactionCtx, TxnType)
from amfa.engine.decide import decide
from amfa.engine.rationale import customer_notice
from amfa.engine.risk import scorer

CALM = {"amount_log": 8.0, "amount_ratio": 1.0, "payee_seen": 0, "device_seen": 12, "payee_age_days": None,
        "history_count": 40,
        "amount_vs_payee_avg": None, "spoof_7d": 0, "failed_factors_24h": 0, "payee_risk_flag": 0,
        "sim_changed_hours": None, "contact_changed_hours": None, "password_reset_hours": None}


def ctx(*, type=TxnType.TRANSFER, new=True, exposure=Band.MEDIUM, amount=Band.MEDIUM, enrolled=None, low_touch=True,
        never_review=False):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=amount, exposureBand=exposure, isNewBeneficiary=new, type=type),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(),
                   enrollment=Enrollment(**(enrolled or {})), policy=Policy(lowTouch=low_touch),
                   org=OrgCtx(neverReview=never_review))


@pytest.fixture
def model_says(monkeypatch):
    def say(level):
        stub = scorer.Result(score=.5, raw=.5, level=level, reasons=[("device_seen", 1.0)], version="stub")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


def test_the_highest_level_gets_every_automated_check_and_no_person(model_says):
    model_says("T4")
    plan = decide(ctx(), features=CALM)
    assert plan.escalate is None and plan.threatLevel == "T4"
    assert set(plan.required) == {Factor.OTP, Factor.UEBA, Factor.FACE, Factor.VOICE}
    assert "automated checks" in plan.reasoning


def test_with_the_switch_off_the_old_behaviour_returns(model_says):
    model_says("T4")
    plan = decide(ctx(low_touch=False), features=CALM)
    assert plan.escalate == "MANUAL_REVIEW" and "threat level T4" in plan.escalateReason


def test_a_missing_check_no_longer_sends_a_high_risk_request_to_a_person(model_says):
    model_says("T3")
    plan = decide(ctx(enrolled={"voice": False, "keystroke": False, "shape": False}), features=CALM)
    assert plan.escalate is None and plan.floorMet
    assert Factor.FACE in plan.required and Factor.VOICE not in plan.required
    old = decide(ctx(enrolled={"voice": False, "keystroke": False, "shape": False}, low_touch=False), features=CALM)
    assert old.escalate == "MANUAL_REVIEW"


def test_a_model_outage_means_the_strongest_checks_not_a_queue(monkeypatch):
    monkeypatch.setattr(scorer, "active", lambda: (_ for _ in ()).throw(scorer.RiskModelError("down")))
    plan = decide(ctx(), features=CALM)
    assert plan.escalate is None and plan.modelError and Factor.FACE in plan.required


def test_if_the_regulatory_floor_cannot_be_met_a_person_still_looks(model_says):
    model_says("T1")
    plan = decide(ctx(enrolled={"face": False, "voice": False, "keystroke": False, "shape": False}), features=CALM)
    assert not plan.floorMet and plan.escalate == "MANUAL_REVIEW" and "policy floor" in plan.escalateReason


def test_a_banks_higher_preference_gives_way_to_what_the_customer_has(model_says):
    """The bank prefers 3 checks for this action, the customer has 2 set up: use both, don't queue it."""
    model_says("T2")
    two_only = {"voice": False, "keystroke": False, "shape": False}
    plan = decide(ctx(enrolled=two_only).model_copy(update={"policy": Policy(actionFloor=ActionFloor.L3)}),
                  features=CALM)
    assert plan.escalate is None and set(plan.required) == {Factor.OTP, Factor.FACE}
    assert "regulatory" not in (plan.escalateReason or "") and "all 2" in plan.autoHandled
    strict = decide(ctx(enrolled=two_only, low_touch=False).model_copy(
        update={"policy": Policy(actionFloor=ActionFloor.L3, lowTouch=False)}), features=CALM)
    assert strict.escalate == "MANUAL_REVIEW"


def test_a_watchlisted_customer_always_goes_to_a_person(model_says):
    model_says("T1")
    watched = ctx().model_copy(update={"org": OrgCtx(segment=Segment.WATCHLIST)})
    plan = decide(watched, features=CALM)
    assert plan.escalate == "MANUAL_REVIEW" and "watchlist" in plan.escalateReason


def test_an_unavailable_light_check_is_dropped_not_swapped_for_a_face_scan(model_says):
    """T1 asks for code + typing; with typing not set up and a light-touch floor, code alone is enough."""
    model_says("T1")
    no_typing = {"keystroke": False, "shape": False}
    light = ctx(new=False, exposure=Band.LOW, amount=Band.LOW, enrolled=no_typing,
                ).model_copy(update={"policy": Policy(actionFloor=ActionFloor.L1)})
    assert set(decide(light, features=CALM | {"payee_seen": 9}).required) == {Factor.OTP}
    kept = decide(light.model_copy(update={"policy": Policy(actionFloor=ActionFloor.L1, lowTouch=False)}),
                  features=CALM | {"payee_seen": 9})
    assert Factor.FACE in kept.required            # the old behaviour: swapped for a face scan


@pytest.mark.parametrize("level", ["T1", "T2", "T4"])
def test_a_payee_on_a_risk_list_always_goes_to_a_person(model_says, level):
    model_says(level)
    plan = decide(ctx(amount=Band.LOW, exposure=Band.LOW), features=CALM | {"payee_risk_flag": 1})
    assert plan.escalate == "MANUAL_REVIEW" and "risk list" in plan.escalateReason


def test_a_hold_beats_the_never_review_waiver(model_says):
    model_says("T2")
    plan = decide(ctx(type=TxnType.ADD_BENEFICIARY, never_review=True), features=CALM | {"payee_risk_flag": 1})
    assert plan.escalate == "MANUAL_REVIEW"


@pytest.mark.parametrize("change", [{"sim_changed_hours": 2.0}, {"contact_changed_hours": 1.0},
                                    {"password_reset_hours": 5.0}])
def test_account_takeover_followed_by_a_cash_out_goes_to_a_person(model_says, change):
    model_says("T2")
    plan = decide(ctx(new=True, exposure=Band.MEDIUM), features=CALM | change)
    assert plan.escalate == "MANUAL_REVIEW" and "changed" in plan.escalateReason


def test_the_same_change_is_fine_for_a_known_payee_or_a_small_amount(model_says):
    model_says("T2")
    sim = CALM | {"sim_changed_hours": 2.0}
    assert decide(ctx(new=False, exposure=Band.MEDIUM), features=sim).escalate is None
    assert decide(ctx(new=True, exposure=Band.LOW, amount=Band.LOW), features=sim).escalate is None


def test_repeated_fake_face_flags_with_failed_checks_go_to_a_person(model_says):
    model_says("T2")
    plan = decide(ctx(), features=CALM | {"spoof_7d": 1, "failed_factors_24h": 2})
    assert plan.escalate == "MANUAL_REVIEW"
    assert decide(ctx(), features=CALM | {"spoof_7d": 1}).escalate is None     # one alone only adds checks


def test_a_very_large_first_payment_asks_for_voice_as_well_as_face(model_says):
    model_says("T2")
    plan = decide(ctx(new=True, exposure=Band.HIGH, amount=Band.HIGH), features=CALM)
    assert {Factor.FACE, Factor.VOICE} <= set(plan.required) and plan.escalate is None


def test_without_voice_set_up_the_large_payment_still_goes_through(model_says):
    model_says("T2")
    plan = decide(ctx(new=True, exposure=Band.HIGH, amount=Band.HIGH, enrolled={"voice": False}), features=CALM)
    assert Factor.VOICE not in plan.required and Factor.FACE in plan.required and plan.escalate is None


def test_a_large_payment_to_a_known_payee_does_not_force_voice(model_says):
    model_says("T2")
    plan = decide(ctx(new=False, exposure=Band.HIGH, amount=Band.HIGH), features=CALM | {"payee_seen": 9})
    assert Factor.VOICE not in plan.required


REGULAR = CALM | {"payee_seen": 6, "payee_age_days": 90.0, "amount_vs_payee_avg": 1.2}


def test_a_regular_payee_at_the_usual_amount_stays_light_even_at_a_medium_amount(model_says):
    model_says("T3")
    plan = decide(ctx(new=False, exposure=Band.MEDIUM), features=REGULAR)
    assert plan.threatLevel == "T1" and plan.modelLevel == "T3" and "regular payee" in plan.capReason
    assert set(plan.required) == {Factor.OTP, Factor.UEBA}


@pytest.mark.parametrize("change", [{"payee_seen": 2}, {"payee_age_days": 10.0}, {"amount_vs_payee_avg": 3.5},
                                    {"device_seen": 0}, {"sim_changed_hours": 3.0}, {"spoof_7d": 1}])
def test_the_regular_payee_relief_needs_everything_to_look_normal(model_says, change):
    model_says("T3")
    plan = decide(ctx(new=False, exposure=Band.MEDIUM), features=REGULAR | change)
    assert plan.threatLevel == "T3"


def test_the_regular_payee_relief_does_not_apply_to_very_large_amounts(model_says):
    model_says("T3")
    assert decide(ctx(new=False, exposure=Band.HIGH, amount=Band.HIGH), features=REGULAR).threatLevel == "T3"


NEWBIE = CALM | {"history_count": 1, "amount_ratio": None, "device_seen": 0}


def test_a_new_customer_is_not_treated_as_a_stranger_for_everything(model_says):
    """With no history every payee, device and hour looks unfamiliar; that is not evidence of fraud."""
    model_says("T4")
    plan = decide(ctx(exposure=Band.MEDIUM), features=NEWBIE)
    assert plan.threatLevel == "T2" and plan.modelLevel == "T4" and "new customer" in plan.capReason
    assert set(plan.required) == {Factor.OTP, Factor.UEBA, Factor.FACE}


@pytest.mark.parametrize("change", [{"history_count": 5}, {"spoof_7d": 1}, {"payee_risk_flag": 1},
                                    {"sim_changed_hours": 2.0}, {"amount_log": None}])
def test_the_new_customer_relief_needs_a_thin_history_and_a_clean_record(model_says, change):
    model_says("T4")
    assert decide(ctx(exposure=Band.MEDIUM), features=NEWBIE | change).threatLevel == "T4"


def test_a_new_customer_still_gets_every_check_on_a_very_large_payment(model_says):
    model_says("T4")
    plan = decide(ctx(exposure=Band.HIGH, amount=Band.HIGH), features=NEWBIE)
    assert plan.threatLevel == "T4" and plan.escalate is None
    assert {Factor.FACE, Factor.VOICE} <= set(plan.required)


def test_large_first_payments_always_carry_the_scam_warning(model_says):
    model_says("T1")
    plan = decide(ctx(new=True, exposure=Band.HIGH, amount=Band.HIGH), features=CALM)
    assert plan.scamRisk and customer_notice(plan, TxnType.TRANSFER)["scamWarning"]
    calm = decide(ctx(new=False, exposure=Band.LOW, amount=Band.LOW), features=CALM | {"payee_seen": 9})
    assert not calm.scamRisk and not customer_notice(calm, TxnType.TRANSFER)["scamWarning"]
