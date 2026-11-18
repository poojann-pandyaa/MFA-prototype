"""#7 phase 3: a candidate risk model scores every payment silently next to the active one. It decides nothing, and nothing it does,
or fails to do, can reach a payment. Its answer is written on the band record so it can earn bands later (phase 4)."""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from amfa.core import db, learning_store
from amfa.engine import band_switch
from amfa.engine.context import (Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, OrgCtx, Policy, TransactionCtx,
                                 TxnType)
from amfa.engine.decide import decide
from amfa.engine.policy import CollectionPolicy, TenantPolicy
from amfa.engine.risk import scorer

IST = ZoneInfo("Asia/Kolkata")
NOON = datetime(2026, 11, 18, 14, 30, tzinfo=IST).timestamp()
V1, V2 = "risk-20261117-v1", "risk-20261201-v2"
PHONE = Enrollment(otp=True, face=True, voice=True, keystroke=True, keystrokeTouch=True, deviceKey=True)


class Stub:
    """A model that answers [level] for every payment, as version [version]; [calls] counts how often it was asked."""
    def __init__(self, version, level="T1", score=0.02, fail=False):
        self.version, self.level, self.score_, self.fail, self.calls = version, level, score, fail, 0

    def score(self, features):
        self.calls += 1
        if self.fail:
            raise RuntimeError("candidate blew up")
        return scorer.Result(score=self.score_, raw=self.score_, level=self.level, reasons=[], version=self.version, habit="T0")


@pytest.fixture
def models(monkeypatch):
    """Which model is active and which is the candidate (None: no candidate)."""
    def use(active, candidate=None):
        monkeypatch.setattr(scorer, "active", lambda: active)
        monkeypatch.setattr(scorer, "candidate", lambda: candidate)
        return active, candidate
    return use


def ctx(*, switch=True, state="COLLECTING", approved=None, exposure=5_00_000_00, new=True, phase="READY", type=TxnType.TRANSFER):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=Band.HIGH, exposureBand=Band.HIGH, exposureMinor=exposure,
                                              isNewBeneficiary=new, type=type),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(), enrollment=PHONE, org=OrgCtx(),
                   policy=Policy(phase=phase, bandSwitch=switch, bandState=state, bandModelVersion=approved,
                                 learningDailyMinor=25_000_00, learningNewPayeeMinor=10_000_00))


DECISION = ("required", "order", "riskBand", "threatLevel", "modelLevel", "escalate", "escalateKind", "floor", "source", "phase",
            "bandState", "bandRulesAdded", "riskScore", "modelVersion", "capReason", "scamRisk", "shadowLevel")


def decision(plan):
    """Everything about a plan that decides or is shown, and the band view without the candidate's two fields."""
    out = {k: getattr(plan, k) for k in DECISION}
    out["bandView"] = {k: v for k, v in (plan.bandView or {}).items() if k not in ("candidateLevel", "candidateVersion")}
    return out


# ---- it watches and decides nothing -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("active_level", ["T0", "T1", "T2", "T3", "T4"])
@pytest.mark.parametrize("candidate_level", ["T0", "T2", "T4"])
@pytest.mark.parametrize("state, approved", [("COLLECTING", None), ("MODEL", V1)])
def test_a_candidate_never_changes_a_decision(models, active_level, candidate_level, state, approved):
    models(Stub(V1, active_level))
    without = decide(ctx(state=state, approved=approved), features={})
    models(Stub(V1, active_level), Stub(V2, candidate_level))
    with_candidate = decide(ctx(state=state, approved=approved), features={})
    assert decision(with_candidate) == decision(without)
    assert with_candidate.bandView["candidateVersion"] == V2


def test_its_answer_is_logged_next_to_the_active_models(models):
    models(Stub(V1, "T0"), Stub(V2, "T3"))
    v = decide(ctx(), features={}).bandView
    assert (v["modelLevel"], v["modelVersion"], v["candidateLevel"], v["candidateVersion"]) == ("T0", V1, "T3", V2)


@pytest.mark.parametrize("level", ["T0", "T1", "T2", "T3", "T4"])
@pytest.mark.parametrize("phase", ["READY", "LEARNING"])
def test_its_answer_is_what_it_would_say_if_it_were_the_active_model(models, level, phase):
    models(Stub(V1, "T1"), Stub(V2, level))
    as_candidate = decide(ctx(phase=phase), features={}).bandView["candidateLevel"]
    models(Stub(V2, level))
    as_active = decide(ctx(phase=phase), features={}).bandView["modelLevel"]
    assert as_candidate == as_active


def test_a_failing_candidate_leaves_no_trace_and_the_payment_is_untouched(models):
    models(Stub(V1, "T2"))
    without = decide(ctx(), features={})
    _, cand = models(Stub(V1, "T2"), Stub(V2, fail=True))
    plan = decide(ctx(), features={})
    assert cand.calls >= 1
    assert decision(plan) == decision(without) and "candidateLevel" not in plan.bandView


def test_a_candidate_that_cannot_be_loaded_is_simply_absent(models, monkeypatch):
    models(Stub(V1, "T2"))
    monkeypatch.setattr(scorer, "candidate", lambda: (_ for _ in ()).throw(RuntimeError("disk gone")))
    plan = decide(ctx(), features={})
    assert "candidateLevel" not in plan.bandView and Factor.FACE in plan.required


def test_a_candidate_reporting_another_version_than_its_own_is_ignored(models):
    class Liar(Stub):
        def score(self, features):
            r = super().score(features)
            return scorer.Result(score=r.score, raw=r.raw, level=r.level, reasons=[], version="risk-someone-else", habit="T0")
    models(Stub(V1), Liar(V2, "T0"))
    assert "candidateLevel" not in decide(ctx(), features={}).bandView


def test_no_candidate_scoring_with_the_switch_off_or_for_account_changes(models):
    _, cand = models(Stub(V1), Stub(V2))
    decide(ctx(switch=False), features={})
    decide(ctx(type=TxnType.PROFILE_CHANGE), features={})
    assert cand.calls == 0


def test_with_no_candidate_nothing_is_added(models):
    models(Stub(V1))
    assert set(decide(ctx(), features={}).bandView) == {"band", "rulesLevel", "modelLevel", "payeeKind", "deviceKind", "hardSign",
                                                       "mode", "modelVersion"}


# ---- the learning row keeps its answer -------------------------------------------------------------------------------------------

@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="K", slug=f"k{uuid.uuid4().hex[:8]}",
                      policy=TenantPolicy(collection=CollectionPolicy(enabled=True)).model_dump(mode="json"))
        c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=t.id, external_ref=uuid.uuid4().hex[:12])
        s.add(t)
        s.flush()
        s.add(c)
    return t.id, c.id


def _txn(s, tenant_id, cust_id, view):
    tid = f"txn_{uuid.uuid4().hex[:12]}"
    s.add(db.Transaction(id=tid, tenant_id=tenant_id, customer_id=cust_id, external_id=tid, idempotency_key=tid, request_hash="x",
                         type="TRANSFER", amount_minor=2_500_00, currency="INR", payee_ref="P", channel="MOBILE",
                         status="AUTHORIZED", created_at=NOON))
    s.flush()
    s.add(db.Decision(plan_id="p", tenant_id=tenant_id, transaction_id=tid, customer_id=cust_id, context={}, preview={},
                      plan={"bandView": view}, source="model", floor="L2", floor_applied=False, risk_band="LOW", reasoning="",
                      counterfactual="", latency_ms=1, policy_version=1, decided_at=NOON))
    s.flush()
    return s.get(db.Transaction, tid)


VIEW = {"band": 1, "rulesLevel": "T1", "modelLevel": "T0", "payeeKind": "KNOWN", "deviceKind": "KNOWN", "hardSign": False,
        "mode": "COLLECTING", "modelVersion": V1}


@pytest.mark.parametrize("level, version, stored", [
    ("T3", V2, ("T3", V2)),
    (None, None, (None, None)),
    ("T3", None, (None, None)),              # a level without its version could be graded for the wrong model
    (None, V2, (None, None)),
    ("T9", V2, (None, None)),
    ("T3", "../" + V2, (None, None)),
    ("T3", "z" * 65, (None, None)),
])
def test_the_row_keeps_the_candidates_level_and_version_both_or_neither(bank, level, version, stored):
    tenant_id, cust_id = bank
    view = VIEW | {k: v for k, v in (("candidateLevel", level), ("candidateVersion", version)) if v is not None}
    with db.session() as s:
        row = learning_store.record(s, _txn(s, tenant_id, cust_id, view), now=NOON)
        assert (row.candidate_level, row.candidate_version) == stored
        assert (row.model_level, row.model_version) == ("T0", V1)          # the active model's own answer is untouched


# ---- installing and removing a candidate ------------------------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    """An empty model store with an active version, and a loader that knows which folders hold a good model."""
    (tmp_path / V1).mkdir()
    (tmp_path / "ACTIVE").write_text(V1)
    good = {V1}

    def load(path):
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name not in good:
            raise scorer.RiskModelError(f"{name} failed its integrity check")
        return type("M", (), {"version": name})()
    monkeypatch.setattr(scorer, "root", lambda: tmp_path)
    monkeypatch.setattr(scorer, "_load", load)

    def add(version, ok=True):
        (tmp_path / version).mkdir()
        if ok:
            good.add(version)
    return tmp_path, add


def test_a_good_version_is_installed_as_the_candidate(store):
    root, add = store
    add(V2)
    assert scorer.set_candidate(V2).version == V2
    assert (root / "CANDIDATE").read_text() == V2
    assert scorer.candidate_version() == V2 and scorer.candidate().version == V2
    assert not list(root.glob(".*.tmp"))                     # written all at once, nothing half-written left behind


def test_installing_another_candidate_replaces_the_first(store):
    _, add = store
    add(V2)
    add("risk-20261215-v3")
    scorer.set_candidate(V2)
    scorer.set_candidate("risk-20261215-v3")
    assert scorer.candidate_version() == "risk-20261215-v3"     # only ever one candidate


@pytest.mark.parametrize("name", ["../" + V2, "a/b", "a\\b", "", ".hidden", "x" * 65, None, 7])
def test_only_a_plain_version_name_can_be_installed(store, name):
    with pytest.raises(scorer.RiskModelError):
        scorer.set_candidate(name)


def test_a_missing_version_the_active_version_or_a_damaged_one_is_refused(store):
    root, add = store
    with pytest.raises(scorer.RiskModelError, match="no model version"):
        scorer.set_candidate(V2)
    with pytest.raises(scorer.RiskModelError, match="active version already"):
        scorer.set_candidate(V1)
    add(V2, ok=False)
    with pytest.raises(scorer.RiskModelError, match="integrity"):
        scorer.set_candidate(V2)
    assert not (root / "CANDIDATE").exists()


def test_clearing_the_candidate_stops_it(store):
    _, add = store
    add(V2)
    scorer.set_candidate(V2)
    assert scorer.clear_candidate() is True and scorer.candidate() is None
    assert scorer.clear_candidate() is False


def test_a_candidate_pointer_that_names_the_active_version_or_a_bad_name_is_no_candidate(store):
    root, add = store
    (root / "CANDIDATE").write_text(V1)
    assert scorer.candidate_version() is None and scorer.candidate() is None
    (root / "CANDIDATE").write_text("../../etc")
    assert scorer.candidate_version() is None and scorer.candidate() is None


def test_a_candidate_that_stops_loading_is_no_candidate_and_does_not_raise(store):
    root, add = store
    add(V2, ok=False)
    (root / "CANDIDATE").write_text(V2)                       # installed earlier; its files have since been changed
    assert scorer.candidate() is None


# ---- training straight into the candidate --------------------------------------------------------------------------------------

def test_training_with_candidate_installs_the_new_version_and_leaves_the_active_one(tmp_path, monkeypatch):
    from amfa.engine.risk import train
    (tmp_path / "ACTIVE").write_text("risk-the-old-one")
    out = train.run(customers=400, days=150, search=[train.SEARCH[0]], seeds=1, force=True, out_root=tmp_path, candidate=True)
    assert (tmp_path / "ACTIVE").read_text() == "risk-the-old-one"
    assert (tmp_path / "CANDIDATE").read_text() == out.name
