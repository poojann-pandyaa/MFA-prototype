"""Phase 2 of the band switch, engine side: the coarse view of a payment that becomes its learning row. Logged only, never decides."""
import pytest

from amfa.engine import band_switch
from amfa.engine.context import (Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, OrgCtx, Plan, Policy,
                                 TransactionCtx, TxnType)
from amfa.engine.decide import decide
from amfa.engine.risk import scorer

F = Factor


def ctx(*, exposure=1_500_00, type=TxnType.TRANSFER, new=False, known_device=True, band=Band.LOW, phase="READY"):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=band, exposureBand=band, exposureMinor=exposure, isNewBeneficiary=new, type=type),
                   device=DeviceCtx(deviceKnown=known_device, locationKnown=True), behavioural=BehaviouralCtx(),
                   enrollment=Enrollment(otp=True, face=True, voice=True, keystroke=True), org=OrgCtx(),
                   policy=Policy(phase=phase))


@pytest.fixture
def model_says(monkeypatch):
    def say(level="T1", score=0.02):
        stub = scorer.Result(score=score, raw=score, level=level, reasons=[], version="stub", habit="T0")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


@pytest.mark.parametrize("required, held, level", [
    ([F.OTP], False, "T0"),
    ([F.OTP, F.UEBA], False, "T1"),
    ([F.OTP, F.FACE], False, "T2"),
    ([F.OTP, F.VOICE], False, "T2"),
    ([F.OTP, F.UEBA, F.FACE, F.VOICE], False, "T3"),
    ([F.OTP], True, "T4"),
])
def test_rules_and_model_are_put_on_the_same_scale(required, held, level):
    assert band_switch.level_of(required, held) == level


def test_the_view_holds_only_the_band_two_levels_and_four_small_categories():
    v = band_switch.view(ctx(), Plan(source="model", threatLevel="T1"), {})
    assert set(v) == {"band", "rulesLevel", "modelLevel", "payeeKind", "deviceKind", "hardSign", "mode", "modelVersion"}
    assert v == {"band": 0, "rulesLevel": "T0", "modelLevel": "T1", "payeeKind": "KNOWN", "deviceKind": "KNOWN", "hardSign": False, "mode": "COLLECTING", "modelVersion": None}


@pytest.mark.parametrize("t", [TxnType.ADD_BENEFICIARY, TxnType.CHANGE_LIMIT, TxnType.PROFILE_CHANGE, TxnType.DEVICE_BINDING])
def test_only_payments_are_collected(t):
    assert band_switch.view(ctx(type=t), Plan(source="model", threatLevel="T1"), {}) is None


@pytest.mark.parametrize("exposure, band", [(1_999_99, 0), (2_000_00, 1), (15_000_00, 2), (1_20_000_00, 3), (None, 3)])
def test_the_band_comes_from_the_day_total_so_splitting_cannot_lower_it_and_unknown_is_the_top(exposure, band):
    assert band_switch.view(ctx(exposure=exposure), Plan(source="model", threatLevel="T1"), {})["band"] == band


def test_the_rules_see_the_payments_collection_band_not_the_bank_setting():
    # Rs 1.2 lakh as the day total: the rules treat it as high value, so a new payee there needs a face check
    v = band_switch.view(ctx(exposure=1_20_000_00, new=True, band=Band.LOW), Plan(source="model", threatLevel="T0"), {})
    assert v["rulesLevel"] == "T2"


def test_categories():
    f = {"payee_risk_flag": 1, "helper_device": 1, "sim_changed_hours": 3}
    v = band_switch.view(ctx(new=True, known_device=False), Plan(source="model", threatLevel="T2"), f)
    assert (v["payeeKind"], v["deviceKind"], v["hardSign"]) == ("FLAGGED", "HELPER", True)
    v = band_switch.view(ctx(new=True, known_device=False), Plan(source="model", threatLevel="T2"), {})
    assert (v["payeeKind"], v["deviceKind"], v["hardSign"]) == ("NEW", "NEW", False)


def test_a_model_that_could_not_score_counts_as_the_top_level_never_as_light():
    assert band_switch.model_level(Plan(source="model", threatLevel="T4", escalate="MANUAL_REVIEW", modelError="x")) == "T4"
    assert band_switch.model_level(Plan(source="learning", shadowLevel="T1", modelLevel=None)) == "T4"
    assert band_switch.model_level(Plan(source="model", threatLevel=None)) == "T4"


def test_while_a_customer_is_learning_the_model_level_is_its_shadow_level():
    assert band_switch.model_level(Plan(source="learning", shadowLevel="T2", modelLevel="T3")) == "T2"


@pytest.mark.parametrize("level, exposure, new", [("T0", 500_00, False), ("T2", 15_000_00, True), ("T4", 2_00_000_00, True)])
def test_decide_logs_the_view_and_the_checks_are_exactly_what_they_are_without_it(model_says, monkeypatch, level, exposure, new):
    model_says(level)
    c = ctx(exposure=exposure, new=new, band=Band.MEDIUM)
    plan = decide(c, features={})
    assert plan.bandView["modelLevel"] in band_switch.LEVELS
    monkeypatch.setattr(band_switch, "view", lambda *a, **k: None)
    bare = decide(c, features={})
    same = ("required", "riskBand", "threatLevel", "escalate", "escalateKind", "floor", "source")
    assert {k: getattr(bare, k) for k in same} == {k: getattr(plan, k) for k in same}


def test_decide_logs_nothing_for_an_account_change(model_says):
    model_says("T1")
    assert decide(ctx(type=TxnType.PROFILE_CHANGE), features={}).bandView is None
