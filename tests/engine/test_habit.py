"""A customer's own habit decides how unusual a payment is: far above it raises the checks, routine large payments do not."""
import pytest

from amfa.engine.context import ActionFloor, Band, BehaviouralCtx, Context, DeviceCtx, Factor, Policy, TransactionCtx
from amfa.engine.decide import decide
from amfa.engine.risk import scorer
from amfa.engine.risk.threat import habit_level, raise_for_habit, routine_relief

CUT = [4.0, 9.0]
ROUTINE = {"habit_surprise": 0.2, "payee_seen": 6, "payee_age_days": 60, "device_seen": 20, "amount_log": 11.0}


@pytest.mark.parametrize("surprise,expect", [(None, "T0"), (0.0, "T0"), (3.99, "T0"), (4.0, "T2"), (8.9, "T2"), (9.0, "T3"), (30, "T3")])
def test_habit_level_follows_how_far_above_their_own_usual_it_is(surprise, expect):
    assert habit_level(surprise, CUT) == expect


def test_no_cutoffs_means_no_opinion():
    assert habit_level(50.0, None) == "T0" and habit_level(50.0, []) == "T0"


def test_habit_only_ever_raises():
    assert raise_for_habit("T1", "T3")[0] == "T3" and raise_for_habit("T3", "T2") == ("T3", None) and raise_for_habit("T2", "T0") == ("T2", None)


def test_a_routine_large_payment_is_kept_light_but_only_when_it_really_is_routine():
    assert routine_relief("T3", ROUTINE)[0] == "T1"
    for change in ({"habit_surprise": 2.0}, {"habit_surprise": None}, {"payee_seen": 2}, {"payee_age_days": 10},
                   {"device_seen": 0}, {"sim_changed_hours": 3}, {"spoof_7d": 1}):
        assert routine_relief("T3", ROUTINE | change) == ("T3", None), change
    assert routine_relief("T1", ROUTINE) == ("T1", None)         # never raises


def ctx(exposure=Band.HIGH, new=False):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=Band.HIGH, exposureBand=exposure, isNewBeneficiary=new),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(), policy=Policy(actionFloor=ActionFloor.L2))


@pytest.fixture
def model_says(monkeypatch):
    def say(level, habit="T0"):
        stub = scorer.Result(score=.5, raw=.5, level=level, reasons=[("amount_ratio", 1.0)], version="stub", habit=habit)
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


def test_a_payment_far_above_their_habit_gets_more_checks_than_the_model_asked(model_says):
    model_says("T1", habit="T3")
    plan = decide(ctx(Band.MEDIUM), features=ROUTINE | {"habit_surprise": 12.0})
    assert plan.threatLevel == "T3" and plan.modelLevel == "T1" and Factor.VOICE in plan.required
    assert "own usual payments" in plan.capReason


def test_a_routine_weekly_large_payment_is_not_an_event_even_in_the_high_band(model_says):
    model_says("T3")
    plan = decide(ctx(Band.HIGH), features=ROUTINE)
    assert plan.threatLevel == "T1" and Factor.FACE not in plan.required and plan.modelLevel == "T3"
    assert decide(ctx(Band.HIGH, new=True), features=ROUTINE).threatLevel == "T3"      # a first payment to a payee is never routine


def test_a_hard_warning_sign_keeps_the_level_whatever_the_habit(model_says):
    model_says("T3")
    assert decide(ctx(), features=ROUTINE | {"sim_changed_hours": 2}).threatLevel == "T3"
