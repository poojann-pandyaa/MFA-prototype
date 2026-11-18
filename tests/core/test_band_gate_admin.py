"""Phase 4 of the band switch, server side: the band's state is read fail-closed; moving a band to the model, turning the switch off
or asking it for less evidence needs two different operators; one is enough to go back to the rules; and the model is watched
after the switch and sent back to the rules on its own."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from amfa.common.web import ApiError
from amfa.core import audit, band_gate, db, learning_store
from amfa.core.api_tenant import apply_policy
from amfa.core.app import create_app
from amfa.core.auth import COOKIE, new_operator_session
from amfa.engine.policy import TenantPolicy

DAY, WEEK = 86_400.0, 7 * 86_400.0
VERSION = "risk-test-v1"                 # the model deciding throughout these tests


@pytest.fixture(autouse=True)
def one_model_decides(monkeypatch):
    monkeypatch.setattr(band_gate, "active_version", lambda: VERSION)


def _policy(**collection):
    p = TenantPolicy().model_dump(mode="json")
    p["collection"] = {"enabled": True, **collection}
    return p


def _tenant(s, policy=None):
    t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="A", slug=f"a{uuid.uuid4().hex[:8]}", policy=policy or _policy())
    s.add(t)
    s.flush()
    return t.id


def _operator(s, tenant_id, role):
    op = db.Operator(id=f"op_{uuid.uuid4().hex[:12]}", tenant_id=tenant_id, email=f"{uuid.uuid4().hex[:10]}@bank.test",
                     password_hash="x", role=role, totp_confirmed=True)
    s.add(op)
    s.flush()
    cookie, csrf = new_operator_session(s, op, "FULL")
    return {"id": op.id, "cookie": cookie, "csrf": csrf}


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        tid = _tenant(s)
        other = _tenant(s)
        ops = {"a": _operator(s, tid, "tenant_admin"), "b": _operator(s, tid, "compliance"), "viewer": _operator(s, tid, "analyst"),
               "other_bank": _operator(s, other, "tenant_admin")}
    return tid, ops


def call(client, op, method, path, json=None):
    client.cookies.set(COOKIE, op["cookie"])
    r = client.request(method, f"/admin/api{path}", json=json, headers={"x-csrf-token": op["csrf"]})
    client.cookies.clear()
    return r


def set_state(tenant_id, band, state, evaluated_at=None, **kw):
    with db.session() as s:
        st = s.get(db.BandGateState, (tenant_id, band))
        if st is None:
            st = db.BandGateState(tenant_id=tenant_id, band=band, figures={}, changed_at=time.time())
            s.add(st)
        st.state, st.evaluated_at = state, evaluated_at if evaluated_at is not None else time.time()
        st.model_version = kw.pop("model_version", VERSION)
        for k, v in kw.items():
            setattr(st, k, v)


def state(tenant_id, band):
    with db.session() as s:
        st = s.get(db.BandGateState, (tenant_id, band))
        return st.state if st else None


# ---- fail closed -----------------------------------------------------------------------------------------------------------------

def test_a_band_with_no_state_is_decided_by_the_rules(bank):
    tid, _ = bank
    with db.session() as s:
        assert band_gate.state_for(s, tid, 0) == "COLLECTING"


def test_an_unknown_state_is_decided_by_the_rules(bank):
    tid, _ = bank
    set_state(tid, 1, "WHATEVER")
    with db.session() as s:
        assert band_gate.state_for(s, tid, 1) == "COLLECTING"


def test_a_model_band_not_graded_for_two_days_is_decided_by_the_rules_again(bank):
    tid, _ = bank
    now = time.time()
    set_state(tid, 2, "MODEL", evaluated_at=now - 47 * 3600)
    with db.session() as s:
        assert band_gate.state_for(s, tid, 2, now) == "MODEL"
    set_state(tid, 2, "MODEL", evaluated_at=now - 49 * 3600)
    with db.session() as s:
        assert band_gate.state_for(s, tid, 2, now) == "COLLECTING"
    set_state(tid, 2, "MODEL", evaluated_at=now + 3600)            # a clock in the future is not "fresh" either
    with db.session() as s:
        assert band_gate.state_for(s, tid, 2, now) == "COLLECTING"


def test_a_state_that_cannot_be_read_is_decided_by_the_rules():
    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("database gone")
    assert band_gate.state_for(Broken(), "tn_x", 0) == "COLLECTING"


# ---- two people to move a band to the model --------------------------------------------------------------------------------------

def test_two_different_operators_move_an_eligible_band_to_the_model(client, bank):
    tid, ops = bank
    set_state(tid, 0, "ELIGIBLE")
    r = call(client, ops["a"], "POST", "/band-gates/0/request-model")
    assert r.status_code == 200 and state(tid, 0) == "ELIGIBLE"                     # one person: nothing changes yet
    rid = r.json()["id"]
    same = call(client, ops["a"], "POST", f"/band-gates/requests/{rid}/approve")
    assert same.status_code == 409 and same.json()["error"]["code"] == "FOUR_EYES" and state(tid, 0) == "ELIGIBLE"
    ok = call(client, ops["b"], "POST", f"/band-gates/requests/{rid}/approve")
    assert ok.status_code == 200 and state(tid, 0) == "MODEL"
    with db.session() as s:
        entry = s.execute(select(db.AuditLog).where(db.AuditLog.action == "band_gate.model",
                                                    db.AuditLog.target == f"{tid}:0")).scalar_one()
        assert (entry.detail["requestedBy"], entry.detail["approvedBy"]) == (ops["a"]["id"], ops["b"]["id"])
    assert audit.verify()["intact"]
    again = call(client, ops["b"], "POST", f"/band-gates/requests/{rid}/approve")
    assert again.status_code == 409 and again.json()["error"]["code"] == "REQUEST_CLOSED"


@pytest.mark.parametrize("st", ["COLLECTING", None])
def test_a_band_that_has_not_earned_the_model_cannot_be_requested(client, bank, st):
    tid, ops = bank
    if st:
        set_state(tid, 1, st)
    r = call(client, ops["a"], "POST", "/band-gates/1/request-model")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ELIGIBLE"


def test_a_band_that_stopped_being_eligible_cannot_be_approved_even_by_a_second_person(client, bank):
    tid, ops = bank
    set_state(tid, 2, "ELIGIBLE")
    rid = call(client, ops["a"], "POST", "/band-gates/2/request-model").json()["id"]
    set_state(tid, 2, "COLLECTING")                                                 # the nightly grading took it back
    r = call(client, ops["b"], "POST", f"/band-gates/requests/{rid}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ELIGIBLE" and state(tid, 2) == "COLLECTING"


def test_a_request_expires(bank):
    tid, ops = bank
    set_state(tid, 0, "ELIGIBLE")
    now = time.time()
    with db.session() as s:
        rid = band_gate.request_model(s, tid, 0, ops["a"]["id"], now).id
    with db.session() as s:
        with pytest.raises(band_gate.GateError) as e:
            band_gate.approve(s, rid, ops["b"]["id"], now + 73 * 3600)
        assert e.value.code == "REQUEST_EXPIRED"
    assert state(tid, 0) == "ELIGIBLE"


def test_operators_without_policy_write_or_from_another_bank_cannot_take_part(client, bank):
    tid, ops = bank
    set_state(tid, 0, "ELIGIBLE")
    assert call(client, ops["viewer"], "POST", "/band-gates/0/request-model").status_code == 403
    rid = call(client, ops["a"], "POST", "/band-gates/0/request-model").json()["id"]
    assert call(client, ops["viewer"], "POST", f"/band-gates/requests/{rid}/approve").status_code == 403
    r = call(client, ops["other_bank"], "POST", f"/band-gates/requests/{rid}/approve")
    assert r.status_code == 403 and r.json()["error"]["code"] == "TENANT_SCOPE"
    assert call(client, ops["other_bank"], "POST", "/band-gates/0/request-model", json=None).status_code == 409   # its own bank: not eligible
    assert state(tid, 0) == "ELIGIBLE"


def test_nobody_can_act_without_signing_in_or_without_the_csrf_token(client, bank):
    tid, ops = bank
    set_state(tid, 0, "ELIGIBLE")
    assert client.post("/admin/api/band-gates/0/request-model").status_code == 401
    client.cookies.set(COOKIE, ops["a"]["cookie"])
    assert client.post("/admin/api/band-gates/0/request-model").status_code == 403         # no CSRF header
    client.cookies.clear()


def test_the_switch_being_off_blocks_requests(bank):
    tid, ops = bank
    set_state(tid, 0, "ELIGIBLE")
    with db.session() as s:
        s.get(db.Tenant, tid).policy = _policy(enabled=False)
    with db.session() as s:
        with pytest.raises(band_gate.GateError) as e:
            band_gate.request_model(s, tid, 0, ops["a"]["id"])
        assert e.value.code == "SWITCH_OFF"


# ---- one person to go back -------------------------------------------------------------------------------------------------------

def test_one_operator_sends_a_band_back_to_the_rules_at_once_and_pending_requests_are_cancelled(client, bank):
    tid, ops = bank
    set_state(tid, 3, "ELIGIBLE")
    rid = call(client, ops["a"], "POST", "/band-gates/3/request-model").json()["id"]
    set_state(tid, 1, "MODEL")
    assert call(client, ops["b"], "POST", "/band-gates/1/revert").json()["changed"] is True
    assert state(tid, 1) == "COLLECTING"
    assert call(client, ops["b"], "POST", "/band-gates/3/revert").json()["changed"] is True
    with db.session() as s:
        assert s.get(db.BandSwitchRequest, rid).status == "CANCELLED"
        assert s.execute(select(db.SecurityEvent).where(db.SecurityEvent.tenant_id == tid,
                                                        db.SecurityEvent.kind == "BAND_SWITCH_FALLBACK")).scalars().first()


# ---- relaxing the switch needs two people ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", [{"enabled": False}, {"gate": {"minGenuine": 4_000}}, {"topGate": {"minFraud": 150}},
                                    {"topGate": {"minGenuine": 9_000, "minFraud": 200}}, {"dailyRowsPerCustomer": 20}])
@pytest.mark.parametrize("platform", [False, True])
def test_no_one_alone_can_relax_the_switch_through_the_policy(bank, change, platform):
    tid, _ = bank
    new = _policy()
    new["collection"] = {**new["collection"], **change}
    with pytest.raises(ApiError) as e:
        apply_policy(tid, TenantPolicy.model_validate(new), "operator:x", None, platform=platform)
    assert e.value.status_code == 409 and e.value.code == "TWO_PERSON_CHANGE"


@pytest.mark.parametrize("change", [{"gate": {"minGenuine": 6_000}}, {"dailyRowsPerCustomer": 5}, {"retentionDays": 200}])
def test_making_it_stricter_needs_no_second_person(bank, change):
    tid, _ = bank
    new = _policy()
    new["collection"] = {**new["collection"], **change}
    assert "collection" in apply_policy(tid, TenantPolicy.model_validate(new), "operator:x", None)["changed"]


def test_turning_it_on_needs_no_second_person():
    db.migrate()
    with db.session() as s:
        tid = _tenant(s, policy=_policy(enabled=False))
    assert "collection" in apply_policy(tid, TenantPolicy.model_validate(_policy()), "apikey:x", None)["changed"]


def test_other_policy_changes_are_unaffected(bank):
    tid, _ = bank
    new = _policy()
    new["sessionTtlSeconds"] = 400
    assert "sessionTtlSeconds" in apply_policy(tid, TenantPolicy.model_validate(new), "apikey:x", None)["changed"]


def test_two_operators_turn_it_off_and_every_band_goes_back_to_the_rules(client, bank):
    tid, ops = bank
    set_state(tid, 0, "MODEL")
    set_state(tid, 1, "ELIGIBLE", streak=6)
    r = call(client, ops["a"], "POST", "/band-gates/policy-request", json={"collection": {**_policy()["collection"], "enabled": False}})
    assert r.status_code == 200
    with db.session() as s:
        assert TenantPolicy.model_validate(s.get(db.Tenant, tid).policy).collection.enabled       # one person: still on
    assert call(client, ops["b"], "POST", f"/band-gates/requests/{r.json()['id']}/approve").status_code == 200
    with db.session() as s:
        t = s.get(db.Tenant, tid)
        assert not TenantPolicy.model_validate(t.policy).collection.enabled
        assert s.execute(select(db.PolicyVersion).where(db.PolicyVersion.tenant_id == tid)).scalars().first().created_by.count("operator:") == 2
    assert (state(tid, 0), state(tid, 1)) == ("COLLECTING", "COLLECTING")      # turning it on again later brings back no approval


def test_a_relaxing_request_is_void_if_the_settings_changed_before_approval(client, bank):
    """The approver must sign off exactly the change shown. Here someone made the switch stricter in between; approving the old
    request would silently have undone that too."""
    tid, ops = bank
    base = _policy()["collection"]
    rid = call(client, ops["a"], "POST", "/band-gates/policy-request",
               json={"collection": {**base, "gate": {"minGenuine": 4_000}}}).json()["id"]
    stricter = _policy()
    stricter["collection"] = {**base, "topGate": {"minGenuine": 20_000, "minFraud": 300}}
    apply_policy(tid, TenantPolicy.model_validate(stricter), "operator:x", None)
    r = call(client, ops["b"], "POST", f"/band-gates/requests/{rid}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "POLICY_CHANGED"
    with db.session() as s:
        c = TenantPolicy.model_validate(s.get(db.Tenant, tid).policy).collection
        assert (c.gate.minGenuine, c.topGate.minFraud) == (5_000, 300)


def test_a_change_that_does_not_relax_is_not_a_two_person_request(client, bank):
    tid, ops = bank
    r = call(client, ops["a"], "POST", "/band-gates/policy-request", json={"collection": {**_policy()["collection"], "dailyRowsPerCustomer": 5}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_RELAXING"


def test_a_relaxing_request_below_the_hard_minimums_is_refused(client, bank):
    tid, ops = bank
    r = call(client, ops["a"], "POST", "/band-gates/policy-request", json={"collection": {"enabled": True, "gate": {"minFraud": 10}}})
    assert r.status_code == 422


# ---- the model is watched after the switch ----------------------------------------------------------------------------------------

def _row(tenant_id, band, week, label, model, rules, mode="MODEL"):
    return db.LearningRow(id=uuid.uuid4().hex, tenant_id=tenant_id, band=band, band_vs_usual="SAME", payee_kind="KNOWN",
                          device_kind="KNOWN", hour_bucket="MORNING", hard_sign=False, rules_level=rules, model_level=model,
                          mode=mode, outcome="COMPLETED", label=label, label_source="bank_ops" if label != "UNKNOWN" else None,
                          week_start=week, model_version=VERSION)


def test_a_model_band_that_loses_two_weeks_in_a_row_goes_back_to_the_rules(bank):
    tid, _ = bank
    now = time.time()
    policy = TenantPolicy.model_validate(_policy())
    set_state(tid, 1, "MODEL")
    first, last = band_gate.window(now, band_gate.ZoneInfo("Asia/Kolkata"), 8)
    with db.session() as s:                                       # in the graded window: the model misses fraud the rules catch
        s.add_all([_row(tid, 1, last, "FRAUD", "T1", "T2") for _ in range(3)])
    with db.session() as s:
        band_gate.evaluate(s, tid, policy, now)
    assert state(tid, 1) == "MODEL"                               # one lost week is not enough
    with db.session() as s:
        band_gate.evaluate(s, tid, policy, now + 2 * 3600)        # the same week again: still one
    assert state(tid, 1) == "MODEL"
    with db.session() as s:
        s.add_all([_row(tid, 1, band_gate.window(now + WEEK, band_gate.ZoneInfo("Asia/Kolkata"), 8)[1], "FRAUD", "T1", "T2")
                   for _ in range(3)])
    with db.session() as s:
        band_gate.evaluate(s, tid, policy, now + WEEK)
    assert state(tid, 1) == "COLLECTING"
    with db.session() as s:
        assert s.execute(select(db.AuditLog).where(db.AuditLog.action == "band_gate.fallback",
                                                   db.AuditLog.target == f"{tid}:1")).scalars().first()


def test_a_model_band_that_wins_keeps_the_model(bank):
    tid, _ = bank
    now = time.time()
    set_state(tid, 1, "MODEL")
    last = band_gate.window(now, band_gate.ZoneInfo("Asia/Kolkata"), 8)[1]
    with db.session() as s:
        s.add_all([_row(tid, 1, last, "FRAUD", "T2", "T1") for _ in range(3)] + [_row(tid, 1, last, "UNKNOWN", "T1", "T2")])
    for w in range(3):
        with db.session() as s:
            band_gate.evaluate(s, tid, TenantPolicy.model_validate(_policy()), now + w * WEEK)
    assert state(tid, 1) == "MODEL"


def test_one_fraud_the_model_let_through_in_the_lakh_band_sends_it_back_at_once(bank):
    """No waiting for the night or for 30 days: the moment the fraud report arrives."""
    tid, _ = bank
    set_state(tid, 3, "MODEL")
    with db.session() as s:
        st = s.get(db.BandGateState, (tid, 3))
        st.figures = {"switchedWeek": "2026-01-05"}
        cust = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=tid, external_ref=uuid.uuid4().hex[:12])
        s.add(cust)
        s.flush()
        txn_id = f"txn_{uuid.uuid4().hex[:12]}"
        s.add(db.Transaction(id=txn_id, tenant_id=tid, customer_id=cust.id, external_id=txn_id, idempotency_key=txn_id,
                             request_hash="x", type="TRANSFER", amount_minor=2_00_000_00, currency="INR", payee_ref="P",
                             channel="MOBILE", status="AUTHORIZED", created_at=time.time()))
        s.flush()
        s.add(db.Decision(plan_id="p", tenant_id=tid, transaction_id=txn_id, customer_id=cust.id, context={}, preview={},
                          plan={"bandView": {"band": 3, "rulesLevel": "T2", "modelLevel": "T1", "payeeKind": "NEW",
                                             "deviceKind": "KNOWN", "hardSign": False, "mode": "MODEL",
                                             "modelVersion": VERSION}},
                          source="model", floor="L2", floor_applied=False, risk_band="LOW", reasoning="", counterfactual="",
                          latency_ms=1, policy_version=1))
        s.flush()
        assert learning_store.record(s, s.get(db.Transaction, txn_id)).mode == "MODEL"
    assert state(tid, 3) == "MODEL"
    with db.session() as s:
        assert learning_store.apply_label(s, txn_id, "FRAUD", "chargeback")
    assert state(tid, 3) == "COLLECTING"
    with db.session() as s:
        ev = s.execute(select(db.SecurityEvent).where(db.SecurityEvent.tenant_id == tid,
                                                      db.SecurityEvent.kind == "BAND_SWITCH_FALLBACK")).scalars().first()
        assert ev.severity == "HIGH"


def test_a_lakh_band_fraud_the_model_caught_does_not_send_it_back(bank):
    tid, _ = bank
    set_state(tid, 3, "MODEL")
    row = _row(tid, 3, "2026-11-09", "FRAUD", "T3", "T2")
    with db.session() as s:
        s.add(row)
        s.flush()
        band_gate.on_fraud_label(s, row)
    assert state(tid, 3) == "MODEL"


def test_a_fraud_in_another_band_never_triggers_the_lakh_band_rule(bank):
    tid, _ = bank
    set_state(tid, 2, "MODEL")
    row = _row(tid, 2, "2026-11-09", "FRAUD", "T1", "T2")
    with db.session() as s:
        s.add(row)
        s.flush()
        band_gate.on_fraud_label(s, row)
    assert state(tid, 2) == "MODEL"        # bands 0-2 go back on the weekly grading, not on one case
