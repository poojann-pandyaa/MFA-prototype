"""The customer's own "no extra checks up to X" setting: what it skips, and what it never skips."""
import pytest

from amfa.engine.context import ActionFloor, Band, BehaviouralCtx, Context, DeviceCtx, Factor, Policy, TransactionCtx
from amfa.engine.decide import decide
from amfa.engine.risk import scorer
from amfa.engine.risk.threat import customer_bypass

KNOWN = {"amount_log": 8.0, "amount_ratio": 1.0, "device_seen": 12}
LIMIT = 5_000_00        # Rs 5,000, in paise


def go(level="T2", f=None, exposure=2_000_00, limit=LIMIT, new=False):
    return customer_bypass(level, KNOWN | (f or {}), limit_minor=limit, exposure_minor=exposure, new_payee=new)


@pytest.mark.parametrize("level", ["T1", "T2", "T3"])
def test_an_ordinary_payment_within_the_limit_skips_the_extra_checks(level):
    assert go(level)[0] == "T0" and "no-extra-check limit" in go(level)[1]


def test_the_limit_counts_everything_sent_in_the_last_day_so_splitting_does_not_help():
    assert go(exposure=LIMIT)[0] == "T0"
    assert go(exposure=LIMIT + 1) == ("T2", None)


@pytest.mark.parametrize("why,kw", [
    ("no limit set", {"limit": None}), ("limit of zero", {"limit": 0}), ("exposure unknown", {"exposure": None}),
    ("first payment to a payee", {"new": True}), ("a phone never used before", {"f": {"device_seen": 0}}),
    ("device unknown", {"f": {"device_seen": None}}), ("not a payment", {"f": {"amount_log": None}}),
    ("a SIM swap two hours ago", {"f": {"sim_changed_hours": 2}}), ("a spoof attempt this week", {"f": {"spoof_7d": 1}}),
    ("a payee on a risk list", {"f": {"payee_risk_flag": 1}}), ("repeated failed checks", {"f": {"failed_factors_24h": 2}}),
    ("three new payees today", {"f": {"new_payees_24h": 3}}),
])
def test_it_never_applies_when(why, kw):
    assert go("T2", **kw) == ("T2", None), why


def test_it_never_skips_a_manual_review_or_raises_a_level():
    assert go("T4") == ("T4", None)
    assert go("T0") == ("T0", None)


# ---- through decide(): the legal minimum and the bank's own floors stay

def ctx(limit, exposure=2_000_00, floor=ActionFloor.L2, new=False, tx=Band.LOW):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=tx, exposureBand=Band.MEDIUM, exposureMinor=exposure, isNewBeneficiary=new),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(), policy=Policy(customerBypassMinor=limit, actionFloor=floor))


@pytest.fixture
def model_says(monkeypatch):
    def say(level):
        stub = scorer.Result(score=.5, raw=.5, level=level, reasons=[("amount_ratio", 1.0)], version="stub")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    return say


def test_a_face_check_the_model_wanted_is_skipped_inside_the_limit(model_says):
    model_says("T2")
    mine = decide(ctx(LIMIT), features=KNOWN)
    plain = decide(ctx(None), features=KNOWN)
    assert Factor.FACE in plain.required and Factor.FACE not in mine.required
    assert mine.threatLevel == "T0" and mine.modelLevel == "T2" and "no-extra-check limit" in mine.capReason


def test_it_never_goes_below_the_legal_minimum(model_says):
    model_says("T3")
    plan = decide(ctx(LIMIT, floor=ActionFloor.L2), features=KNOWN)
    assert plan.threatLevel == "T0" and len(plan.required) >= 2          # two factors, one dynamic, still asked


def test_above_the_limit_nothing_changes(model_says):
    model_says("T2")
    plan = decide(ctx(LIMIT, exposure=LIMIT + 100), features=KNOWN)
    assert Factor.FACE in plan.required and plan.threatLevel == "T2" and not plan.capReason
