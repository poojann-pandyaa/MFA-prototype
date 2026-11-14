"""While the model does not know a customer yet: the phone's fingerprint or screen lock, limits, hard signs, and a hold only when sure."""
import pytest

from amfa.engine.context import (ActionFloor, Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, OrgCtx, Policy,
                                 TransactionCtx, dynamic_factors, strength)
from amfa.engine.decide import decide
from amfa.engine.risk import scorer

PHONE_CUSTOMER = Enrollment(otp=True, face=False, voice=False, keystroke=True, keystrokeTouch=True, deviceKey=True)
CLEAN = {"amount_log": 7.5, "amount_ratio": 1.0, "device_seen": 0, "history_count": 0}


def ctx(*, phase="LEARNING", exposure=2_000_00, new=False, enroll=PHONE_CUSTOMER, two=True, floor=ActionFloor.L2, band=Band.LOW):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=band, exposureBand=band, exposureMinor=exposure, isNewBeneficiary=new),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(), enrollment=enroll,
                   org=OrgCtx(deviceCountsTwo=two),
                   policy=Policy(actionFloor=floor, phase=phase, learningDailyMinor=25_000_00, learningNewPayeeMinor=10_000_00))


@pytest.fixture
def model_says(monkeypatch):
    def say(level="T1", score=0.02, habit="T0"):
        stub = scorer.Result(score=score, raw=score, level=level, reasons=[("amount_ratio", 1.0)], version="stub", habit=habit)
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


def test_a_new_customer_confirms_a_usual_payment_with_the_phones_fingerprint_alone(model_says):
    model_says("T3")                                        # even a model that is wary of an unknown customer does not ask more of them
    plan = decide(ctx(), features=CLEAN)
    assert plan.required == [Factor.DEVICE] and plan.escalate is None and plan.phase == "LEARNING"
    assert plan.modelLevel == "T3" and plan.shadowLevel                        # the model's view is recorded for readiness
    assert plan.floorMet                                                        # the key counts as the phone plus the person: two factors


def test_a_key_that_counts_once_needs_a_second_factor_to_meet_the_minimum(model_says):
    model_says()
    plan = decide(ctx(two=False), features=CLEAN)
    assert Factor.DEVICE in plan.required and len(plan.required) == 2 and plan.floorMet


def test_no_face_or_voice_is_ever_asked_while_learning(model_says):
    model_says("T4", score=0.2)
    for kw in ({}, {"exposure": 90_000_00}, {"new": True}):
        plan = decide(ctx(**kw), features=CLEAN)
        assert not {Factor.FACE, Factor.VOICE} & set(plan.required), kw


def test_a_payment_beyond_the_limits_adds_a_code(model_says):
    model_says()
    daily = decide(ctx(exposure=40_000_00), features=CLEAN)
    first_payment = decide(ctx(exposure=15_000_00, new=True), features=CLEAN)
    small_first = decide(ctx(exposure=6_000_00, new=True), features=CLEAN)
    assert set(daily.required) == {Factor.DEVICE, Factor.OTP} and "daily limit" in daily.capReason
    assert set(first_payment.required) == {Factor.DEVICE, Factor.OTP} and "new payee" in first_payment.capReason
    assert small_first.required == [Factor.DEVICE]


def test_a_hard_warning_sign_adds_a_code_from_day_one(model_says):
    model_says()
    for sign in ({"sim_changed_hours": 3}, {"password_reset_hours": 2}, {"payee_risk_flag": 1}, {"spoof_7d": 1}):
        plan = decide(ctx(), features=CLEAN | sign)
        assert set(plan.required) == {Factor.DEVICE, Factor.OTP}, sign


def test_a_hold_only_when_the_model_is_nearly_sure(model_says):
    model_says("T4", score=0.4)
    assert decide(ctx(), features=CLEAN).escalate is None             # T4 but not sure: learning does not hold on that alone
    model_says("T4", score=0.7)
    plan = decide(ctx(), features=CLEAN)
    assert plan.escalate == "MANUAL_REVIEW" and "nearly sure" in plan.escalateReason


def test_learning_does_not_need_the_model(monkeypatch):
    def broken():
        raise scorer.RiskModelError("no model")
    monkeypatch.setattr(scorer, "active", broken)
    plan = decide(ctx(), features=CLEAN)
    assert plan.required == [Factor.DEVICE] and plan.escalate is None        # a ready customer would fail closed; a learning one does not


def test_a_customer_whose_phone_cannot_sign_keeps_the_normal_plan_not_a_code_that_cannot_meet_the_minimum(model_says):
    """A browser, or a phone with no screen lock: the light plan would be a code alone, below the two-factor minimum, so every
    payment would end in review. They are treated as before the learning phase existed."""
    model_says("T1")
    no_key = Enrollment(otp=True, face=False, voice=False, keystroke=True, keystrokeTouch=True, deviceKey=False)
    plan = decide(ctx(enroll=no_key), features=CLEAN)
    assert plan.source == "model" and plan.phase == "READY" and Factor.DEVICE not in plan.required and plan.floorMet


def test_once_ready_the_model_decides_again(model_says):
    model_says("T2")
    ready = decide(ctx(phase="READY", enroll=Enrollment(deviceKey=True), band=Band.MEDIUM), features=CLEAN)
    assert ready.source == "model" and ready.phase == "READY" and Factor.FACE in ready.required


def test_the_device_key_is_a_challenge_answering_factor_and_counts_by_the_banks_choice():
    assert Factor.DEVICE in dynamic_factors([Factor.DEVICE, Factor.UEBA]) and dynamic_factors([Factor.OTP, Factor.DEVICE]) == [Factor.OTP, Factor.DEVICE]
    assert strength([Factor.DEVICE], OrgCtx(deviceCountsTwo=True)) == 2 and strength([Factor.DEVICE], OrgCtx()) == 1


def test_a_learning_customer_is_told_what_is_happening_not_the_models_unfamiliarity_reasons(model_says):
    from amfa.engine.rationale import customer_notice
    from amfa.engine.context import TxnType
    model_says("T3")
    usual = customer_notice(decide(ctx(), features=CLEAN), TxnType.TRANSFER, CLEAN)
    over = customer_notice(decide(ctx(exposure=40_000_00), features=CLEAN), TxnType.TRANSFER, CLEAN)
    assert "getting to know how you pay" in usual["reason"] and "device is new" not in usual["reason"] and not usual["scamWarning"]
    assert "one-time code" in over["reason"] and over["scamWarning"]
