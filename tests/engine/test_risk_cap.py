"""Proportionate friction: a small payment that is usual for the customer is held at T1, unless a hard
warning sign is present. The model's own level is always kept on the plan for audit."""
import pytest

from amfa.engine.context import Band, BehaviouralCtx, Context, DeviceCtx, Factor, FLOOR_MINIMUM, Policy, TransactionCtx
from amfa.engine.decide import decide
from amfa.engine.risk import scorer
from amfa.engine.risk.threat import LEVELS, hard_signals, small_payment_cap

USUAL = {"amount_log": 7.0, "amount_ratio": 1.2}


def ctx(band=Band.LOW, cap=True):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=Band.LOW, exposureBand=band), device=DeviceCtx(),
                   behavioural=BehaviouralCtx(), policy=Policy(smallPaymentCap=cap))


@pytest.fixture
def model_says(monkeypatch):
    def say(level):
        stub = scorer.Result(score=.5, raw=.5, level=level, reasons=[("device_seen", 1.0)], version="stub")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


@pytest.mark.parametrize("level", ["T2", "T3", "T4"])
def test_small_usual_payment_is_held_at_t1(level):
    assert small_payment_cap(level, USUAL) == ("T1", "small payment within this customer's usual, kept at T1")


@pytest.mark.parametrize("level", ["T0", "T1"])
def test_it_never_raises_a_level(level):
    assert small_payment_cap(level, USUAL) == (level, None)


@pytest.mark.parametrize("signal", [
    {"spoof_7d": 1}, {"failed_factors_24h": 2}, {"payee_risk_flag": 1}, {"sim_changed_hours": 47.9},
    {"password_reset_hours": 3}, {"contact_changed_hours": 20}, {"sensitive_actions_24h": 1}, {"new_payees_24h": 3}])
def test_a_hard_signal_keeps_the_models_level(signal):
    assert hard_signals(USUAL | signal) and small_payment_cap("T3", USUAL | signal) == ("T3", None)


def test_signals_just_outside_their_limits_do_not_count():
    f = USUAL | {"failed_factors_24h": 1, "sim_changed_hours": 48, "password_reset_hours": 24,
                 "contact_changed_hours": 24, "new_payees_24h": 2, "spoof_7d": 0}
    assert hard_signals(f) == [] and small_payment_cap("T3", f)[0] == "T1"


def test_an_unusually_large_amount_keeps_the_level():
    assert small_payment_cap("T3", USUAL | {"amount_ratio": 5.0}) == ("T3", None)
    assert small_payment_cap("T3", USUAL | {"amount_ratio": 4.9})[0] == "T1"
    assert small_payment_cap("T3", {"amount_log": 6.0, "amount_ratio": None})[0] == "T1"   # no history yet


def test_account_changes_are_never_lenient():
    assert small_payment_cap("T3", {"amount_log": None, "amount_ratio": None}) == ("T3", None)


def test_plan_records_both_levels_and_still_meets_the_floor(model_says):
    model_says("T3")
    plan = decide(ctx(), features=USUAL)
    assert plan.threatLevel == "T1" and plan.modelLevel == "T3" and "kept at T1" in plan.capReason
    assert plan.escalate is None and Factor.OTP in plan.required and len(plan.required) >= FLOOR_MINIMUM[plan.floor]
    assert "Model said T3" in plan.reasoning


def test_t4_small_payment_is_not_sent_to_manual_review(model_says):
    model_says("T4")
    plan = decide(ctx(), features=USUAL)
    assert plan.threatLevel == "T1" and plan.modelLevel == "T4" and plan.escalate is None


@pytest.mark.parametrize("band,cap", [(Band.MEDIUM, True), (Band.HIGH, True), (None, True), (Band.LOW, False)])
def test_no_leniency_unless_small_and_enabled(model_says, band, cap):
    model_says("T3")
    plan = decide(ctx(band, cap), features=USUAL)
    assert plan.threatLevel == "T3" and plan.capReason is None


def test_a_hard_signal_beats_a_small_amount_end_to_end(model_says):
    model_says("T3")
    assert decide(ctx(), features=USUAL | {"sim_changed_hours": 3}).threatLevel == "T3"


def test_model_failure_still_fails_closed_even_for_a_small_payment(monkeypatch):
    monkeypatch.setattr(scorer, "active", lambda: (_ for _ in ()).throw(scorer.RiskModelError("gone")))
    plan = decide(ctx(), features=USUAL)
    assert plan.escalate == "MANUAL_REVIEW" and plan.threatLevel == "T4" and plan.capReason is None


def test_levels_are_ordered():
    assert LEVELS.index(small_payment_cap("T4", USUAL)[0]) < LEVELS.index("T4")
