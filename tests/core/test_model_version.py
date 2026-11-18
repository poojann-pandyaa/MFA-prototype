"""#7 phase 1: every band record and band state notes which risk model version it belongs to. Recording only: no decision changes."""
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, inspect, select, text

from amfa.core import band_gate, db, learning_store
from amfa.engine import band_switch
from amfa.engine.context import (Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, OrgCtx, Plan, Policy, TransactionCtx,
                                 TxnType)
from amfa.engine.decide import decide
from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy
from amfa.engine.risk import scorer

IST = ZoneInfo("Asia/Kolkata")
NOON = datetime(2026, 11, 18, 14, 30, tzinfo=IST).timestamp()


@pytest.fixture
def model(monkeypatch):
    def use(version="risk-20261117-v1", level="T1"):
        stub = scorer.Result(score=0.02, raw=0.02, level=level, reasons=[], version=version, habit="T0")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub, "version": version})())
    return use


def ctx(state="COLLECTING", phase="READY", enroll=None):
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=Band.LOW, exposureBand=Band.LOW, exposureMinor=1_500_00, type=TxnType.TRANSFER),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(), enrollment=enroll or Enrollment(), org=OrgCtx(),
                   policy=Policy(phase=phase, bandSwitch=True, bandState=state, learningDailyMinor=25_000_00,
                                 learningNewPayeeMinor=10_000_00))


# ---- the decision logs the version that scored it -------------------------------------------------------------------------------

def test_the_view_carries_the_version_of_the_model_that_scored_the_payment(model):
    model("risk-20261117-v1")
    assert decide(ctx(), features={}).bandView["modelVersion"] == "risk-20261117-v1"
    model("risk-20261201-v2")
    assert decide(ctx(), features={}).bandView["modelVersion"] == "risk-20261201-v2"


def test_a_model_that_could_not_score_leaves_no_version(monkeypatch):
    def broken():
        raise scorer.RiskModelError("no model")
    monkeypatch.setattr(scorer, "active", broken)
    assert decide(ctx(), features={}).bandView["modelVersion"] is None


def test_a_learning_customer_whose_score_failed_leaves_no_version(monkeypatch):
    def broken():
        raise scorer.RiskModelError("no model")
    monkeypatch.setattr(scorer, "active", broken)
    phone = Enrollment(otp=True, face=False, voice=False, keystroke=False, keystrokeTouch=True, deviceKey=True)
    plan = decide(ctx(phase="LEARNING", enroll=phone), features={})
    assert plan.source == "learning" and plan.bandView["modelVersion"] is None


@pytest.mark.parametrize("version", ["", None, "x" * 65, 7])
def test_an_odd_version_string_is_recorded_as_none(version):
    assert band_switch.model_version(Plan(source="model", threatLevel="T1", modelVersion=version if isinstance(version, str) else "")) is None


def test_recording_the_version_changes_no_decision(model, monkeypatch):
    model("risk-20261117-v1", level="T2")
    with_version = decide(ctx(), features={})
    monkeypatch.setattr(band_switch, "model_version", lambda plan: None)
    without = decide(ctx(), features={})
    keys = ("required", "riskBand", "threatLevel", "escalate", "floor", "source", "bandRulesAdded", "bandState")
    assert {k: getattr(with_version, k) for k in keys} == {k: getattr(without, k) for k in keys}


# ---- the learning row stores it ----------------------------------------------------------------------------------------------------

@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="V", slug=f"v{uuid.uuid4().hex[:8]}",
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
        "mode": "COLLECTING"}


@pytest.mark.parametrize("given, stored", [("risk-20261117-v1", "risk-20261117-v1"), (None, None), ("", None), (17, None),
                                           ("y" * 65, None)])
def test_the_row_stores_the_version_or_nothing(bank, given, stored):
    tenant_id, cust_id = bank
    with db.session() as s:
        row = learning_store.record(s, _txn(s, tenant_id, cust_id, VIEW | {"modelVersion": given}), now=NOON)
        assert row is not None and row.model_version == stored


def test_a_row_from_before_versions_were_logged_still_writes_with_no_version(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        assert learning_store.record(s, _txn(s, tenant_id, cust_id, VIEW), now=NOON).model_version is None


# ---- an older database gains the columns and keeps its rows ---------------------------------------------------------------------

def test_the_upgrade_adds_the_version_columns_to_an_older_database_and_keeps_its_rows(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    db.Base.metadata.create_all(eng)
    db.BandBase.metadata.create_all(eng)                     # the band switch's tables, as the core creates them when it runs them
    with eng.begin() as c:                                   # make it look like a database from before this change
        c.execute(text("DROP INDEX IF EXISTS ix_learning_row_model_version"))
        c.execute(text("ALTER TABLE learning_row DROP COLUMN model_version"))
        c.execute(text("ALTER TABLE band_gate DROP COLUMN model_version"))
        c.execute(text("INSERT INTO learning_row (id, tenant_id, band, band_vs_usual, payee_kind, device_kind, hour_bucket, hard_sign, "
                       "rules_level, model_level, mode, outcome, label, week_start) VALUES ('r1', 't', 1, 'SAME', 'KNOWN', 'KNOWN', "
                       "'MORNING', 0, 'T1', 'T0', 'COLLECTING', 'COMPLETED', 'UNKNOWN', '2026-11-16')"))
        c.execute(text("INSERT INTO band_gate (tenant_id, band, state, streak, changed_at, figures, losing) "
                       "VALUES ('t', 2, 'MODEL', 4, 1.0, '{}', 0)"))
    assert "model_version" not in {c["name"] for c in inspect(eng).get_columns("learning_row")}
    added = db.upgrade_columns(eng)
    assert {"learning_row.model_version", "band_gate.model_version"} <= set(added)
    with eng.connect() as c:
        assert c.execute(text("SELECT id, band, model_version FROM learning_row")).all() == [("r1", 1, None)]
        assert c.execute(text("SELECT state, streak, model_version FROM band_gate")).all() == [("MODEL", 4, None)]
    assert db.upgrade_columns(eng) == []                      # running it again changes nothing


def test_the_schema_version_moved_on():
    assert db.SCHEMA_VERSION == 6
    assert {(t, c) for v, t, c, _ in db.UPGRADES if v == 6} == {("learning_row", "model_version"), ("band_gate", "model_version"),
                                                                 ("learning_row", "candidate_level"),
                                                                 ("learning_row", "candidate_version")}


# ---- the band state notes the version it was graded or approved on ------------------------------------------------------------

MIN = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4)


def _policy():
    return TenantPolicy(collection=CollectionPolicy(enabled=True, gate=MIN, topGate=MIN))


def test_a_grading_notes_the_version_it_ran_under(bank, monkeypatch):
    tenant_id, _ = bank
    monkeypatch.setattr(band_gate, "active_version", lambda: "risk-20261117-v1")
    with db.session() as s:
        out = band_gate.evaluate(s, tenant_id, _policy(), NOON)
        assert {st.model_version for st in out.values()} == {"risk-20261117-v1"}


def test_with_no_loadable_model_nothing_is_graded_and_nothing_moves(bank, monkeypatch):
    tenant_id, _ = bank
    monkeypatch.setattr(band_gate, "active_version", lambda: None)
    with db.session() as s:
        assert band_gate.evaluate(s, tenant_id, _policy(), NOON) == {}
        assert s.get(db.BandGateState, (tenant_id, 0)) is None


def test_an_approved_band_keeps_the_version_it_was_approved_on_through_later_gradings(bank, monkeypatch):
    tenant_id, _ = bank
    monkeypatch.setattr(band_gate, "active_version", lambda: "risk-20261117-v1")
    with db.session() as s:
        s.add(db.BandGateState(tenant_id=tenant_id, band=1, state="ELIGIBLE", streak=4, figures={}, changed_at=NOON,
                               evaluated_at=time.time(), model_version="risk-20261117-v1"))
    with db.session() as s:
        rid = band_gate.request_model(s, tenant_id, 1, "op_a").id
    with db.session() as s:
        band_gate.approve(s, rid, "op_b")
        st = s.get(db.BandGateState, (tenant_id, 1))
        assert (st.state, st.model_version) == ("MODEL", "risk-20261117-v1")
        entry = s.execute(select(db.AuditLog).where(db.AuditLog.action == "band_gate.model",
                                                    db.AuditLog.target == f"{tenant_id}:1")).scalar_one()
        assert entry.detail["modelVersion"] == "risk-20261117-v1"
    with db.session() as s:                                  # later gradings by the same version never overwrite it
        band_gate.evaluate(s, tenant_id, _policy(), NOON)
        band_gate.evaluate(s, tenant_id, _policy(), NOON + 86_400)
        assert s.get(db.BandGateState, (tenant_id, 1)).model_version == "risk-20261117-v1"


def test_the_console_shows_each_bands_version(bank, monkeypatch):
    tenant_id, _ = bank
    monkeypatch.setattr(band_gate, "active_version", lambda: "risk-20261117-v1")
    with db.session() as s:
        band_gate.evaluate(s, tenant_id, _policy(), NOON)
        assert {b["modelVersion"] for b in band_gate.view(s, tenant_id, _policy())} == {"risk-20261117-v1"}


def test_active_version_reads_the_deciding_model_and_never_raises(monkeypatch):
    monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"version": "risk-x"})())
    assert band_gate.active_version() == "risk-x"

    def broken():
        raise scorer.RiskModelError("gone")
    monkeypatch.setattr(scorer, "active", broken)
    assert band_gate.active_version() is None
