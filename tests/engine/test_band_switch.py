"""Phase 4 of the band switch, engine side. The main security test: until a band is on the model, the plan is never weaker than the
model's plan, for any payment; floors, hard signs and holds are the same either way; and with the switch off nothing changes."""
import itertools

import pytest

from amfa.engine import band_switch
from amfa.engine.context import (ActionFloor, Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, OrgCtx, Policy,
                                 TransactionCtx, TxnType, dynamic_factors)
from amfa.engine.decide import decide
from amfa.engine.risk import scorer

ORDER = {Band.LOW: 0, Band.MEDIUM: 1, Band.HIGH: 2}
EVERYTHING = Enrollment(otp=True, face=True, voice=True, keystroke=True, keystrokeTouch=True, deviceKey=True)
PHONE_ONLY = Enrollment(otp=True, face=False, voice=False, keystroke=False, shape=False, keystrokeTouch=True, deviceKey=True)
CODE_ONLY = Enrollment(otp=True, face=False, voice=False, keystroke=False, shape=False)


@pytest.fixture
def model_says(monkeypatch):
    def say(level="T1", score=0.02):
        stub = scorer.Result(score=score, raw=score, level=level, reasons=[("amount_ratio", 1.0)], version="stub", habit="T0")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


def ctx(*, state="COLLECTING", switch=True, exposure=1_500_00, new=False, known=True, place=True, odd=False, bband=Band.LOW,
        dormant=False, enroll=EVERYTHING, floor=ActionFloor.L2, type=TxnType.TRANSFER, phase="READY", bank_band=Band.LOW):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=bank_band, exposureBand=bank_band, exposureMinor=exposure,
                                              isNewBeneficiary=new, type=type),
                   device=DeviceCtx(deviceKnown=known, locationKnown=place, hourUnusual=odd),
                   behavioural=BehaviouralCtx(band=bband, dormant=dormant), enrollment=enroll, org=OrgCtx(),
                   policy=Policy(actionFloor=floor, phase=phase, bandSwitch=switch, bandState=state,
                                 learningDailyMinor=25_000_00, learningNewPayeeMinor=10_000_00))


def at_least_as_strong(a, b):
    """Plan a asks for everything plan b does, its risk band is no lower, and it holds whenever b holds."""
    return (set(b.required) <= set(a.required) and ORDER[a.riskBand] >= ORDER[b.riskBand]
            and (b.escalate is None or a.escalate == b.escalate))


GRID = list(itertools.product(
    ("T0", "T1", "T2", "T3", "T4"),                          # what the model says
    (500_00, 5_000_00, 50_000_00, 5_00_000_00, None),        # bands 0, 1, 2, 3 and an unknown day total
    (False, True),                                           # new payee
    (True, False),                                           # known device
    (True, False),                                           # known place
    (False, True),                                           # odd hour
    (Band.LOW, Band.MEDIUM, Band.HIGH),                      # behaviour
))


@pytest.mark.parametrize("enroll", [EVERYTHING, PHONE_ONLY, CODE_ONLY], ids=["everything", "phone", "code"])
def test_collecting_is_never_weaker_than_the_model_for_any_payment(model_says, enroll):
    weaker = []
    for level, exposure, new, known, place, odd, bband in GRID:
        model_says(level)
        kw = dict(exposure=exposure, new=new, known=known, place=place, odd=odd, bband=bband, enroll=enroll)
        collecting = decide(ctx(state="COLLECTING", **kw), features={})
        on_model = decide(ctx(state="MODEL", **kw), features={})
        if not at_least_as_strong(collecting, on_model):
            weaker.append((level, exposure, new, known, place, odd, bband))
    assert not weaker, f"collecting was weaker than the model in {len(weaker)} cases, e.g. {weaker[:3]}"


def test_and_the_rules_really_add_checks_where_the_model_is_light(model_says):
    model_says("T0")
    collecting = decide(ctx(state="COLLECTING", exposure=5_00_000_00, new=True), features={})
    on_model = decide(ctx(state="MODEL", exposure=5_00_000_00, new=True), features={})
    assert Factor.FACE in collecting.required and Factor.FACE not in on_model.required
    assert collecting.bandRulesAdded and collecting.bandState == "COLLECTING" and on_model.bandState == "MODEL"


@pytest.mark.parametrize("state", ["COLLECTING", "ELIGIBLE", None, "", "model", "SOMETHING"])
def test_anything_but_model_is_decided_with_the_rules_as_the_minimum(model_says, state):
    model_says("T0")
    plan = decide(ctx(state=state, exposure=5_00_000_00, new=True), features={})
    assert Factor.FACE in plan.required


@pytest.mark.parametrize("level", ["T0", "T2", "T4"])
def test_the_floors_hard_signs_and_holds_are_the_same_in_both_modes(model_says, level):
    model_says(level)
    hard = {"sim_changed_hours": 2, "payee_risk_flag": 1}
    for floor in (ActionFloor.L2, ActionFloor.L3):
        a = decide(ctx(state="COLLECTING", floor=floor, exposure=50_000_00), features=hard)
        b = decide(ctx(state="MODEL", floor=floor, exposure=50_000_00), features=hard)
        assert a.floor == b.floor
        assert (a.escalate or "") == (b.escalate or "") or b.escalate is None
        for p in (a, b):
            assert any(f in p.required for f in dynamic_factors(OrgCtx().permitted))       # the two-factor minimum, one live
            assert len(p.required) >= 2


@pytest.mark.parametrize("level", ["T0", "T1", "T3", "T4"])
def test_with_the_switch_off_the_plan_is_exactly_what_it_was(model_says, monkeypatch, level):
    model_says(level)
    c = ctx(switch=False, state="COLLECTING", exposure=5_00_000_00, new=True, known=False)
    with_step = decide(c, features={})
    monkeypatch.setattr(band_switch, "apply", lambda plan, ctx: plan)
    without = decide(c, features={})
    keys = ("required", "riskBand", "threatLevel", "escalate", "floor", "source", "order")
    assert {k: getattr(with_step, k) for k in keys} == {k: getattr(without, k) for k in keys}
    assert with_step.bandState is None and with_step.bandRulesAdded == []


@pytest.mark.parametrize("t", [TxnType.ADD_BENEFICIARY, TxnType.CHANGE_LIMIT, TxnType.PROFILE_CHANGE, TxnType.DEVICE_BINDING])
def test_account_changes_are_never_touched_by_the_band_switch(model_says, monkeypatch, t):
    model_says("T1")
    c = ctx(state="COLLECTING", type=t, exposure=5_00_000_00)
    a = decide(c, features={})
    monkeypatch.setattr(band_switch, "apply", lambda plan, ctx: plan)
    assert decide(c, features={}).required == a.required and a.bandState is None


def test_a_customer_still_learning_keeps_the_learning_plan(model_says):
    model_says("T3")
    kw = dict(phase="LEARNING", enroll=PHONE_ONLY, exposure=5_000_00)
    a, b = decide(ctx(state="COLLECTING", **kw), features={}), decide(ctx(state="MODEL", **kw), features={})
    assert a.source == b.source == "learning" and a.required == b.required and a.bandRulesAdded == []


def test_the_learning_row_records_the_mode_the_payment_was_decided_under(model_says):
    model_says("T1")
    assert decide(ctx(state="MODEL"), features={}).bandView["mode"] == "MODEL"
    assert decide(ctx(state="ELIGIBLE"), features={}).bandView["mode"] == "ELIGIBLE"
    assert decide(ctx(state="junk"), features={}).bandView["mode"] == "COLLECTING"


def test_a_model_that_cannot_score_still_fails_closed_in_both_modes(monkeypatch):
    """As today: with the model down, every check the customer has (the bank's low-touch setting), never a light plan."""
    def broken():
        raise scorer.RiskModelError("no model")
    monkeypatch.setattr(scorer, "active", broken)
    off = decide(ctx(switch=False), features={})
    for state in ("COLLECTING", "MODEL"):
        p = decide(ctx(state=state), features={})
        assert set(p.required) == {Factor.OTP, Factor.UEBA, Factor.FACE, Factor.VOICE} and p.riskBand is Band.HIGH
        assert set(p.required) == set(off.required) and p.escalate == off.escalate
