"""Proportionate friction: a small payment that is usual for the customer is held at T1, unless a hard
warning sign is present, the model is confident (T3 or above), or, from Rs 2,000 sent in 24 h, the payee is not
established. The model's own level is always kept on the plan for audit."""
import pytest

from amfa.engine.context import Band, BehaviouralCtx, Context, DeviceCtx, Factor, FLOOR_MINIMUM, Policy, TransactionCtx
from amfa.engine.decide import decide
from amfa.engine.policy import TenantPolicy
from amfa.engine.risk import scorer
from amfa.engine.risk.threat import LEVELS, hard_signals, needs_established_payee, small_payment_cap

USUAL = {"amount_log": 7.0, "amount_ratio": 1.2}
ESTABLISHED = {"payee_seen": 4, "payee_age_days": 45.0}


def ctx(band=Band.LOW, cap=True, exposure=1_500_00, payee_from=2_000_00):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=Band.LOW, exposureBand=band, exposureMinor=exposure), device=DeviceCtx(),
                   behavioural=BehaviouralCtx(), policy=Policy(smallPaymentCap=cap, smallPaymentEstablishedPayeeFromMinor=payee_from))


@pytest.fixture
def model_says(monkeypatch):
    def say(level):
        stub = scorer.Result(score=.5, raw=.5, level=level, reasons=[("device_seen", 1.0)], version="stub")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


def test_small_usual_payment_is_held_at_t1():
    assert small_payment_cap("T2", USUAL) == ("T1", "small payment within this customer's usual, kept at T1")


@pytest.mark.parametrize("level", ["T3", "T4"])
def test_a_confident_model_is_never_set_aside_for_a_small_amount(level):
    assert small_payment_cap(level, USUAL) == (level, None)
    assert small_payment_cap(level, USUAL | ESTABLISHED, payee_check=True) == (level, None)


@pytest.mark.parametrize("level", ["T0", "T1"])
def test_it_never_raises_a_level(level):
    assert small_payment_cap(level, USUAL) == (level, None)


@pytest.mark.parametrize("signal", [
    {"spoof_7d": 1}, {"failed_factors_24h": 2}, {"payee_risk_flag": 1}, {"sim_changed_hours": 47.9},
    {"password_reset_hours": 3}, {"contact_changed_hours": 20}, {"sensitive_actions_24h": 1}, {"new_payees_24h": 3}])
def test_a_hard_signal_keeps_the_models_level(signal):
    assert hard_signals(USUAL | signal) and small_payment_cap("T2", USUAL | signal) == ("T2", None)


def test_signals_just_outside_their_limits_do_not_count():
    f = USUAL | {"failed_factors_24h": 1, "sim_changed_hours": 48, "password_reset_hours": 24,
                 "contact_changed_hours": 24, "new_payees_24h": 2, "spoof_7d": 0}
    assert hard_signals(f) == [] and small_payment_cap("T2", f)[0] == "T1"


def test_an_unusually_large_amount_keeps_the_level():
    assert small_payment_cap("T2", USUAL | {"amount_ratio": 5.0}) == ("T2", None)
    assert small_payment_cap("T2", USUAL | {"amount_ratio": 4.9})[0] == "T1"
    assert small_payment_cap("T2", {"amount_log": 6.0, "amount_ratio": None})[0] == "T1"   # no history yet


def test_account_changes_are_never_lenient():
    assert small_payment_cap("T2", {"amount_log": None, "amount_ratio": None}) == ("T2", None)


@pytest.mark.parametrize("payee,capped", [
    ({}, False),                                              # never paid
    ({"payee_seen": 6, "payee_age_days": 29.9}, False),       # paid often, but only for the last few weeks
    ({"payee_seen": 1, "payee_age_days": 30.0}, True),
    (ESTABLISHED, True)])
def test_from_the_payee_line_the_cap_needs_an_established_payee(payee, capped):
    assert small_payment_cap("T2", USUAL | payee, payee_check=True)[0] == ("T1" if capped else "T2")
    assert small_payment_cap("T2", USUAL | payee)[0] == "T1"     # below the line the payee does not matter


@pytest.mark.parametrize("exposure,line,needed", [
    (1_999_99, 2_000_00, False), (2_000_00, 2_000_00, True), (9_999_99, 2_000_00, True),
    (None, 2_000_00, True),                                   # an unknown total is never small
    (50_00, 0, True), (9_999_99, None, False)])
def test_where_the_payee_line_applies(exposure, line, needed):
    assert needs_established_payee(exposure, line) is needed


def test_the_bank_setting_defaults_to_rs_2000():
    assert TenantPolicy().smallPaymentEstablishedPayeeFromMinor == 2_000_00 == Policy().smallPaymentEstablishedPayeeFromMinor


def test_plan_records_both_levels_and_still_meets_the_floor(model_says):
    model_says("T2")
    plan = decide(ctx(), features=USUAL)
    assert plan.threatLevel == "T1" and plan.modelLevel == "T2" and "kept at T1" in plan.capReason
    assert plan.escalate is None and Factor.OTP in plan.required and len(plan.required) >= FLOOR_MINIMUM[plan.floor]
    assert "Model said T2" in plan.reasoning


def test_t4_small_payment_keeps_every_automated_check(model_says):
    model_says("T4")
    plan = decide(ctx(), features=USUAL)
    assert plan.threatLevel == "T4" and plan.capReason is None
    assert {Factor.FACE, Factor.VOICE} <= set(plan.required) and plan.escalate is None    # low-touch: checks, not a person


def test_a_coached_scam_to_a_recent_payee_is_not_held_light(model_says):
    """The case this rule exists for: Rs 7,000 from the customer's own phone, under 5x their usual, to an account they first
    paid three weeks ago. The model says T2; from Rs 2,000 the payee is not established enough to hold it at T1."""
    model_says("T2")
    scam = USUAL | {"amount_ratio": 4.5, "payee_seen": 3, "payee_age_days": 21.0, "device_seen": 12}
    assert decide(ctx(exposure=7_000_00), features=scam).threatLevel == "T2"
    assert decide(ctx(exposure=7_000_00), features=scam | {"payee_age_days": 120.0}).threatLevel == "T1"
    assert decide(ctx(exposure=7_000_00, payee_from=None), features=scam).threatLevel == "T1"     # the bank turned it off


@pytest.mark.parametrize("band,cap", [(Band.MEDIUM, True), (Band.HIGH, True), (None, True), (Band.LOW, False)])
def test_no_leniency_unless_small_and_enabled(model_says, band, cap):
    model_says("T2")
    plan = decide(ctx(band, cap), features=USUAL)
    assert plan.threatLevel == "T2" and plan.capReason is None


def test_a_hard_signal_beats_a_small_amount_end_to_end(model_says):
    model_says("T2")
    assert decide(ctx(), features=USUAL | {"sim_changed_hours": 3}).threatLevel == "T2"


def test_model_failure_still_fails_closed_even_for_a_small_payment(monkeypatch):
    monkeypatch.setattr(scorer, "active", lambda: (_ for _ in ()).throw(scorer.RiskModelError("gone")))
    plan = decide(ctx(), features=USUAL)
    assert plan.threatLevel == "T4" and plan.capReason is None and Factor.FACE in plan.required
    assert plan.escalate is None                      # low-touch: every automated check, not a person
    strict = decide(ctx().model_copy(update={"policy": Policy(smallPaymentCap=True, lowTouch=False)}), features=USUAL)
    assert strict.escalate == "MANUAL_REVIEW"


def test_levels_are_ordered():
    assert LEVELS.index(small_payment_cap("T2", USUAL)[0]) < LEVELS.index("T2")
