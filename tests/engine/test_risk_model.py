"""Training, loading and the engine's use of the risk model (trains a small model once)."""
import shutil

import pytest

from amfa.engine.context import (Band, BehaviouralCtx, Context, DeviceCtx, Factor, FLOOR_MINIMUM, Policy,
                                 TransactionCtx)
from amfa.engine.decide import decide
from amfa.engine.risk import scorer, train
from amfa.engine.risk.features import H, Event, Profile, build
from amfa.engine.risk.threat import LEVELS

NOW = 1_780_000_000.0


@pytest.fixture(scope="module")
def model_dir():
    out = train.run(customers=1500, search=[train.SEARCH[0]], seeds=1, force=True)
    scorer._load.cache_clear()
    return out


def ctx(low_touch=True):
    return Context(userId="cu_1", transactionId="txn_1", transaction=TransactionCtx(amountBand=Band.LOW),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(), policy=Policy(lowTouch=low_touch))


D = 24 * H
# Riya: a year (the model's whole memory) of ~Rs 2,000 evening payments to three payees, from her own phone
# at home in Pune.
HISTORY = [Event(at=NOW - d * D, hour=19, amount=[2000.0, 1500.0, 2500.0, 1800.0][d % 4], type="TRANSFER",
                 channel="MOBILE", payee=["mom", "grocer", "landlord"][d % 3], device="riya-phone",
                 network="home-wifi", place="IN:Pune", country="IN") for d in range(2, 365, 2)]
PROFILE = Profile(account_created_at=NOW - 800 * D, enrolled_factors=4, device_key_bound=True, ueba_risk=.15)


def routine():
    """A: Rs 2,100 to Mom at 7 pm, her phone, home Wi-Fi."""
    cur = Event(at=NOW, hour=19, amount=2100.0, type="TRANSFER", channel="MOBILE", payee="mom",
                device="riya-phone", network="home-wifi", place="IN:Pune", country="IN")
    return build(cur, HISTORY, PROFILE)


def new_phone_travelling():
    """B: genuine. New phone while in Delhi, Rs 2,500 to her usual grocer at 8 pm."""
    cur = Event(at=NOW, hour=20, amount=2500.0, type="TRANSFER", channel="MOBILE", payee="grocer",
                device="riya-newphone", network="hotel-wifi", place="IN:Delhi", country="IN")
    return build(cur, HISTORY, Profile(**{**PROFILE.__dict__, "device_key_bound": False, "ueba_risk": .3}))


def takeover():
    """C: SIM swapped 3 h ago, then Rs 1,80,000 to an unknown payee at 1 am from an unknown phone."""
    cur = Event(at=NOW, hour=1, amount=180000.0, type="TRANSFER", channel="MOBILE", payee="unknown",
                device="x-phone", network="x-net", place="IN:Delhi", country="IN")
    return build(cur, HISTORY, Profile(**{**PROFILE.__dict__, "device_key_bound": False, "ueba_risk": .65,
                                          "sim_changed_at": NOW - 3 * H}))


def test_model_card_and_manifest(model_dir):
    m = scorer.active()
    assert m.version == model_dir.name and len(m.cutoffs) == 4 and m.cutoffs == sorted(m.cutoffs)
    assert {"pr_auc", "baseline_logistic_pr_auc", "by_scenario", "by_segment"} <= set(m.card["metrics"])


def test_tampered_model_is_refused(model_dir, tmp_path):
    bad = tmp_path / model_dir.name
    shutil.copytree(model_dir, bad)
    with open(bad / "booster_0.txt", "a") as f:
        f.write("\n")
    with pytest.raises(scorer.RiskModelError, match="integrity"):
        scorer.RiskModel(bad)


def test_riya_examples(model_dir):
    m = scorer.active()
    a, b, c = m.score(routine()), m.score(new_phone_travelling()), m.score(takeover())
    rank = LEVELS.index
    assert rank(a.level) <= 1                # routine: no extra checks
    assert rank(c.level) >= 3                # SIM-swap takeover: at least face + voice
    assert a.raw < b.raw < c.raw             # compare the unrounded model output: tiny probabilities round to 0
    assert c.reasons and all(v > 0 for _, v in c.reasons)


@pytest.mark.xfail(strict=False, reason="open decision: T4 is the riskiest 0.2% of genuine payments by rank; "
                   "it should also need a high absolute fraud probability, or a genuine new phone in a new "
                   "city can land in manual review")
def test_genuine_new_phone_while_travelling_is_not_sent_to_manual_review(model_dir):
    assert 1 <= LEVELS.index(scorer.active().score(new_phone_travelling()).level) <= 3


def test_decide_uses_the_model_and_keeps_the_regulatory_floor(model_dir):
    plan = decide(ctx(), features=routine())
    assert plan.source == "model" and plan.threatLevel in LEVELS and plan.modelVersion == model_dir.name
    assert plan.rulesBaseline and plan.escalate is None
    assert len(plan.required) >= FLOOR_MINIMUM[plan.floor] and Factor.OTP in plan.required
    assert plan.reasoning.startswith(f"threat {plan.threatLevel}")


def test_no_features_fails_closed(model_dir):
    plan = decide(ctx(low_touch=False), features=None)
    assert plan.escalate == "MANUAL_REVIEW" and plan.threatLevel == "T4" and plan.modelError
    low = decide(ctx(), features=None)      # low-touch: the strongest checks instead of a person
    assert low.escalate is None and low.threatLevel == "T4" and low.modelError and Factor.FACE in low.required


def test_missing_model_fails_closed(model_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(scorer, "root", lambda: tmp_path)
    plan = decide(ctx(low_touch=False), features=routine())
    assert plan.escalate == "MANUAL_REVIEW" and "no active risk model" in plan.modelError
    assert decide(ctx(), features=routine()).escalate is None


def test_t4_goes_to_manual_review_unless_low_touch(model_dir, monkeypatch):
    stub = scorer.Result(score=.97, raw=.9, level="T4", reasons=[("sim_changed_hours", 1.2)], version="stub")
    monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub})())
    plan = decide(ctx(low_touch=False), features=routine())
    assert plan.escalate == "MANUAL_REVIEW" and "T4" in plan.escalateReason
    assert {Factor.FACE, Factor.VOICE} <= set(plan.required)
    low = decide(ctx(), features=routine())    # T4 on its own is answered with every automated check
    assert low.escalate is None and {Factor.FACE, Factor.VOICE} <= set(low.required)


def test_a_model_trained_on_a_different_history_window_is_refused(model_dir, tmp_path):
    import json
    from amfa.engine.risk.scorer import MANIFEST, sha256
    other = tmp_path / model_dir.name
    shutil.copytree(model_dir, other)
    card = json.loads((other / "card.json").read_text())
    card["historyDays"] = 90
    (other / "card.json").write_text(json.dumps(card))
    manifest = json.loads((other / MANIFEST).read_text())
    manifest["files"]["card.json"] = sha256(other / "card.json")      # a legitimate, re-signed card
    (other / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(scorer.RiskModelError, match="90-day history window"):
        scorer.RiskModel(other)
