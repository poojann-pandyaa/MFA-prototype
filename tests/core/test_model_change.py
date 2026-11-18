"""#7 phase 2: the safety net. A band's grading and approval belong to one risk model version. When the version deciding changes, by
any route, nothing stays trusted on a version that was not graded: at the nightly grading and at every decision."""
import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from amfa.core import audit, band_gate, db
from amfa.engine import band_switch
from amfa.engine.context import (Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, OrgCtx, Policy, TransactionCtx,
                                 TxnType)
from amfa.engine.decide import decide
from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy
from amfa.engine.risk import scorer

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 18, 14, 30, tzinfo=IST).timestamp()
V1, V2 = "risk-20261117-v1", "risk-20261201-v2"
MIN = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4)
POLICY = TenantPolicy(collection=CollectionPolicy(enabled=True, gate=MIN, topGate=MIN))


@pytest.fixture
def deciding(monkeypatch):
    """Which model version decides now (as band_gate sees it)."""
    def use(version):
        monkeypatch.setattr(band_gate, "active_version", lambda: version)
    return use


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="C", slug=f"c{uuid.uuid4().hex[:8]}", policy=POLICY.model_dump(mode="json"))
        s.add(t)
    return t.id


def set_state(tenant_id, band, state, version, **kw):
    with db.session() as s:
        st = db.BandGateState(tenant_id=tenant_id, band=band, state=state, model_version=version, streak=kw.pop("streak", 4),
                              figures={}, changed_at=NOW, evaluated_at=kw.pop("evaluated_at", NOW))
        s.add(st)


def state(tenant_id, band):
    with db.session() as s:
        st = s.get(db.BandGateState, (tenant_id, band))
        return st.state, st.model_version, st.streak


def evaluate(tenant_id, now=NOW):
    with db.session() as s:
        return {b: st.state for b, st in band_gate.evaluate(s, tenant_id, POLICY, now).items()}


def rows(tenant_id, band, version, n_genuine, n_fraud, week=None):
    week = week or band_gate.window(NOW, IST, 8)[1]
    with db.session() as s:
        for i in range(n_genuine + n_fraud):
            fraud = i >= n_genuine
            s.add(db.LearningRow(id=uuid.uuid4().hex, tenant_id=tenant_id, band=band, band_vs_usual="SAME", payee_kind="KNOWN",
                                 device_kind="KNOWN", hour_bucket="MORNING", hard_sign=False, rules_level="T2", model_level="T2",
                                 model_version=version, mode="COLLECTING", outcome="COMPLETED",
                                 label="FRAUD" if fraud else "UNKNOWN", label_source="bank_ops" if fraud else None, week_start=week))


def audit_entries(tenant_id, action):
    with db.session() as s:
        return s.execute(select(db.AuditLog).where(db.AuditLog.tenant_id == tenant_id, db.AuditLog.action == action)).scalars().all()


def events(tenant_id, kind="BAND_MODEL_CHANGED"):
    with db.session() as s:
        return s.execute(select(db.SecurityEvent).where(db.SecurityEvent.tenant_id == tenant_id,
                                                        db.SecurityEvent.kind == kind)).scalars().all()


# ---- grading counts only the deciding version's rows -----------------------------------------------------------------------------

def test_only_the_rows_the_deciding_version_scored_are_graded(bank, deciding):
    rows(bank, 0, V1, 40, 4)
    rows(bank, 0, V2, 12, 2)
    rows(bank, 0, None, 30, 3)                                # from before versions were logged: never graded
    deciding(V2)
    evaluate(bank)
    with db.session() as s:
        f = s.get(db.BandGateState, (bank, 0)).figures
    assert (f["genuine"], f["fraud"]) == (12, 2)
    deciding(V1)
    evaluate(bank)
    with db.session() as s:
        f = s.get(db.BandGateState, (bank, 0)).figures
    assert (f["genuine"], f["fraud"]) == (40, 4)


def test_with_no_loadable_model_nothing_is_graded_and_a_model_band_is_left_to_go_stale(bank, deciding):
    set_state(bank, 2, "MODEL", V1, evaluated_at=NOW - 3600)
    deciding(None)
    assert evaluate(bank) == {}
    assert state(bank, 2)[:2] == ("MODEL", V1)
    with db.session() as s:
        assert band_gate.decision_state(s, bank, 2, NOW) == ("MODEL", V1)                   # still fresh
        assert band_gate.decision_state(s, bank, 2, NOW + 48 * 3600) == ("COLLECTING", None)   # not graded for two days


# ---- a version change sends trusted bands back to the rules -------------------------------------------------------------------------

def test_a_band_on_the_model_goes_back_to_the_rules_when_another_version_decides(bank, deciding):
    set_state(bank, 1, "MODEL", V1)
    with db.session() as s:
        s.add(db.BandSwitchRequest(id=f"bsr_{uuid.uuid4().hex[:12]}", tenant_id=bank, kind="BAND_TO_MODEL", band=1,
                                   payload={"modelVersion": V1}, status="PENDING", requested_by="op_a", requested_at=NOW,
                                   expires_at=NOW + 3600))
    deciding(V2)
    evaluate(bank)
    assert state(bank, 1) == ("COLLECTING", V2, 0)
    [entry] = audit_entries(bank, "band_gate.model_changed")
    assert (entry.detail["from"], entry.detail["fromVersion"], entry.detail["toVersion"]) == ("MODEL", V1, V2)
    [ev] = events(bank)
    assert ev.severity == "MEDIUM" and V1 in ev.message and V2 in ev.message
    with db.session() as s:
        assert {r.status for r in s.execute(select(db.BandSwitchRequest).where(db.BandSwitchRequest.tenant_id == bank)).scalars()} \
            == {"CANCELLED"}
    assert audit.verify()["intact"]


def test_the_lakh_band_changing_model_is_a_high_severity_event(bank, deciding):
    set_state(bank, 3, "MODEL", V1)
    deciding(V2)
    evaluate(bank)
    assert [e.severity for e in events(bank)] == ["HIGH"]


def test_an_eligible_band_goes_back_too_but_raises_no_event(bank, deciding):
    set_state(bank, 0, "ELIGIBLE", V1)
    deciding(V2)
    evaluate(bank)
    assert state(bank, 0) == ("COLLECTING", V2, 0)
    assert len(audit_entries(bank, "band_gate.model_changed")) == 1 and events(bank) == []


def test_a_band_still_collecting_only_moves_its_version(bank, deciding):
    set_state(bank, 0, "COLLECTING", V1, streak=2)
    deciding(V2)
    evaluate(bank)
    assert state(bank, 0)[:2] == ("COLLECTING", V2)
    assert audit_entries(bank, "band_gate.model_changed") == []


def test_the_same_version_keeps_its_bands(bank, deciding):
    set_state(bank, 1, "MODEL", V1)
    set_state(bank, 2, "ELIGIBLE", V1)
    deciding(V1)
    evaluate(bank)
    assert state(bank, 1)[0] == "MODEL" and state(bank, 2)[0] in ("ELIGIBLE", "COLLECTING")
    assert audit_entries(bank, "band_gate.model_changed") == []


def test_going_back_to_the_old_version_does_not_bring_its_approval_back(bank, deciding):
    set_state(bank, 1, "MODEL", V1)
    deciding(V2)
    evaluate(bank)
    deciding(V1)
    evaluate(bank, NOW + 86_400)
    assert state(bank, 1)[0] == "COLLECTING"                 # it must earn it and be approved by two people again


# ---- the decision checks the version that scored the payment ------------------------------------------------------------------------

def test_the_decision_state_of_a_model_band_carries_the_approved_version(bank):
    set_state(bank, 1, "MODEL", V1)
    set_state(bank, 2, "MODEL", None)
    set_state(bank, 0, "ELIGIBLE", V1)
    with db.session() as s:
        assert band_gate.decision_state(s, bank, 1, NOW) == ("MODEL", V1)
        assert band_gate.decision_state(s, bank, 2, NOW) == ("COLLECTING", None)        # on MODEL but no approved version
        assert band_gate.decision_state(s, bank, 0, NOW) == ("ELIGIBLE", None)
        assert band_gate.decision_state(s, bank, 3, NOW) == ("COLLECTING", None)        # no state at all


@pytest.fixture
def model_says(monkeypatch):
    def say(version, level="T0"):
        stub = scorer.Result(score=0.01, raw=0.01, level=level, reasons=[], version=version, habit="T0")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub, "version": version})())
    return say


def ctx(approved, state="MODEL"):
    """A large first payment to a new payee: the rules ask for a face check, a light model does not."""
    return Context(userId="cu_1", transactionId="txn_1",
                   transaction=TransactionCtx(amountBand=Band.HIGH, exposureBand=Band.HIGH, exposureMinor=5_00_000_00,
                                              isNewBeneficiary=True, type=TxnType.TRANSFER),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(), enrollment=Enrollment(), org=OrgCtx(),
                   policy=Policy(bandSwitch=True, bandState=state, bandModelVersion=approved))


def test_a_model_band_is_trusted_only_with_the_version_it_was_approved_on(model_says):
    model_says(V1)
    trusted = decide(ctx(V1), features={})
    assert trusted.bandState == "MODEL" and trusted.bandRulesAdded == [] and trusted.bandView["mode"] == "MODEL"
    model_says(V2)                                            # new weights, loaded behind the band switch's back
    swapped = decide(ctx(V1), features={})
    assert swapped.bandState == "COLLECTING" and Factor.FACE in swapped.required and swapped.bandView["mode"] == "COLLECTING"


@pytest.mark.parametrize("approved", [None, ""])
def test_a_model_band_with_no_approved_version_is_decided_by_the_rules(model_says, approved):
    model_says(V1)
    plan = decide(ctx(approved), features={})
    assert plan.bandState == "COLLECTING" and Factor.FACE in plan.required


def test_a_model_that_cannot_score_never_matches_an_approved_version(monkeypatch):
    def broken():
        raise scorer.RiskModelError("gone")
    monkeypatch.setattr(scorer, "active", broken)
    assert decide(ctx(V1), features={}).bandState == "COLLECTING"


@pytest.mark.parametrize("level", ["T0", "T1", "T2", "T3", "T4"])
def test_a_wrong_version_is_never_weaker_than_the_right_one(model_says, level):
    model_says(V2, level)
    wrong = decide(ctx(V1), features={})
    right = decide(ctx(V2), features={})
    assert set(right.required) <= set(wrong.required)
    assert band_switch.BAND_ORDER[wrong.riskBand] >= band_switch.BAND_ORDER[right.riskBand]
    assert right.escalate is None or wrong.escalate is not None


# ---- approvals belong to a version ------------------------------------------------------------------------------------------------

def test_a_request_records_the_version_it_was_made_under(bank, deciding):
    set_state(bank, 0, "ELIGIBLE", V1)
    deciding(V1)
    with db.session() as s:
        r = band_gate.request_model(s, bank, 0, "op_a")
        assert r.payload == {"modelVersion": V1}


def test_no_request_without_a_loadable_model(bank, deciding):
    set_state(bank, 0, "ELIGIBLE", V1)
    deciding(None)
    with db.session() as s, pytest.raises(band_gate.GateError) as e:
        band_gate.request_model(s, bank, 0, "op_a")
    assert e.value.code == "NO_MODEL"


def test_a_band_graded_on_another_version_cannot_be_requested(bank, deciding):
    set_state(bank, 0, "ELIGIBLE", V1)                        # the night's grading has not run yet since the change
    deciding(V2)
    with db.session() as s, pytest.raises(band_gate.GateError) as e:
        band_gate.request_model(s, bank, 0, "op_a")
    assert e.value.code == "MODEL_CHANGED"


def test_a_request_made_under_one_version_cannot_be_approved_under_another(bank, deciding):
    set_state(bank, 0, "ELIGIBLE", V1)
    deciding(V1)
    with db.session() as s:
        rid = band_gate.request_model(s, bank, 0, "op_a").id
    deciding(V2)
    with db.session() as s:
        with pytest.raises(band_gate.GateError) as e:
            band_gate.approve(s, rid, "op_b")
        assert e.value.code == "MODEL_CHANGED"
    assert state(bank, 0)[0] == "ELIGIBLE"                    # nothing moved


def test_an_approval_puts_the_band_on_the_version_that_was_asked_for(bank, deciding):
    set_state(bank, 0, "ELIGIBLE", V1)
    deciding(V1)
    with db.session() as s:
        rid = band_gate.request_model(s, bank, 0, "op_a").id
    with db.session() as s:
        band_gate.approve(s, rid, "op_b")
    assert state(bank, 0)[:2] == ("MODEL", V1)


def test_a_pending_request_for_another_version_is_replaced(bank, deciding):
    set_state(bank, 0, "ELIGIBLE", V2)
    old = f"bsr_{uuid.uuid4().hex[:12]}"
    with db.session() as s:
        s.add(db.BandSwitchRequest(id=old, tenant_id=bank, kind="BAND_TO_MODEL", band=0, payload={"modelVersion": V1},
                                   status="PENDING", requested_by="op_a", requested_at=NOW, expires_at=time.time() + 3600))
    deciding(V2)
    with db.session() as s:
        new = band_gate.request_model(s, bank, 0, "op_a")
        assert new.id != old and new.payload == {"modelVersion": V2}
        assert s.get(db.BandSwitchRequest, old).status == "CANCELLED"
