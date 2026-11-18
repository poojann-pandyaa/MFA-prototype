"""Phase 2 of the band switch, end to end: payments made through the Demo Bank against a real in-process core write their anonymous
learning rows when they settle, labels reach them, and nothing about the payment itself changes."""
import importlib
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from amfa import config
from amfa.core import api_admin, db, learning_store, orchestrator
from amfa.core.app import create_app
from amfa.core.auth import DEFAULT_SCOPES
from amfa.engine.policy import TenantPolicy
# the customer, check helpers and scripted services of the customer-app tests
from tests.core.test_customer_app import LEVEL, _txn, act, app, balance, fresh_rate_limits, held_after_checks, scripted, verify  # noqa: F401


@pytest.fixture(scope="module")
def stack():
    """Its own bank tenant. The Demo Bank module reads its tenant once, at import, so point it at this one and put it back after."""
    base = config.get().public_url
    with TestClient(create_app(), base_url=base) as core:
        tenant = api_admin.create_tenant("Band Bank", f"band{uuid.uuid4().hex[:6]}", "test")
        key = api_admin.create_api_key(tenant["id"], "bank", list(DEFAULT_SCOPES), "test")
        saved = {k: os.environ.get(k) for k in ("AMFA_BASE_URL", "AMFA_KEY_ID", "AMFA_KEY_SECRET", "AMFA_TENANT_ID", "BANK_SESSION_SECRET")}
        os.environ.update(AMFA_BASE_URL=base, AMFA_KEY_ID=key["keyId"], AMFA_KEY_SECRET=key["secret"],
                          AMFA_TENANT_ID=tenant["id"], BANK_SESSION_SECRET="test-session-secret")
        os.environ.pop("BANK_STATE_FILE", None)
        bank = importlib.import_module("examples.demo_bank.bank")
        old = (bank.AMFA, bank.TENANT)
        bank.AMFA, bank.TENANT = bank.AmfaClient(base, key["keyId"], key["secret"], http=core), tenant["id"]
        try:
            yield SimpleNamespace(core=core, bank=bank, tenant=tenant)
        finally:
            bank.AMFA, bank.TENANT = old
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _switch(stack, on: bool):
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        p = TenantPolicy.model_validate(t.policy).model_dump(mode="json")
        p["collection"]["enabled"] = on
        t.policy = p


@pytest.fixture(autouse=True)
def switch_on(stack):
    _switch(stack, True)
    yield
    _switch(stack, False)


def _row(action_id):
    with db.session() as s:
        txn = _txn(s, action_id)
        return txn.id, s.execute(select(db.LearningRow).where(db.LearningRow.link == learning_store.link_of(txn.id))).scalars().first()


def _pay(stack, app, rupees, payee="BEN-42"):
    started = act(app, "transfer", payeeRef=payee, amountRupees=rupees).json()
    verify(stack, started["challengeUrl"])
    done = app.post(f"/api/actions/{started['action']['id']}/complete").json()
    assert done["action"]["status"] == "DONE"
    return started["action"]["id"]


def test_a_completed_payment_writes_one_anonymous_row(stack, app):
    aid = _pay(stack, app, 2_500)
    _, row = _row(aid)
    assert row is not None
    assert (row.band, row.outcome, row.label, row.mode) == (1, "COMPLETED", "UNKNOWN", "COLLECTING")
    assert row.model_version == "stub"                   # the version of the (scripted) model that scored the payment
    assert row.rules_level in ("T0", "T1", "T2", "T3", "T4") and row.model_level in ("T0", "T1", "T2", "T3", "T4")


def test_the_band_follows_the_days_total_so_a_split_payment_is_recorded_in_the_higher_band(stack, app):
    _pay(stack, app, 7_500)
    second = _pay(stack, app, 7_500)
    assert _row(second)[1].band == 2


def test_this_was_not_me_labels_the_row_as_fraud(stack, app):
    aid = _pay(stack, app, 500)
    assert app.post(f"/api/actions/{aid}/report", json={"reason": "not me"}).json()["action"]["reported"]
    _, row = _row(aid)
    assert (row.label, row.label_source) == ("FRAUD", "customer_report")


def test_a_rejected_review_is_a_declined_row_labelled_fraud(stack, app):
    action = held_after_checks(stack, app)
    with db.session() as s:
        txn = _txn(s, action["id"])
        case = s.execute(select(db.ReviewCase).where(db.ReviewCase.transaction_id == txn.id)).scalar_one()
        orchestrator.review_decision(s, case, approve=False, operator=SimpleNamespace(id="op_x"), note="not her")
    _, row = _row(action["id"])
    assert (row.outcome, row.label, row.label_source) == ("DECLINED", "FRAUD", "review_verdict")


def test_a_review_approved_by_two_people_is_a_completed_row_labelled_genuine(stack, app):
    action = held_after_checks(stack, app)
    with db.session() as s:
        txn = _txn(s, action["id"])
        case = s.execute(select(db.ReviewCase).where(db.ReviewCase.transaction_id == txn.id)).scalar_one()
        orchestrator.review_decision(s, case, approve=True, operator=SimpleNamespace(id="op_a"), note="ok")
        assert _row(action["id"])[1] is None                         # one approval: not settled yet, no row
        orchestrator.review_decision(s, case, approve=True, operator=SimpleNamespace(id="op_b"), note="ok")
    _, row = _row(action["id"])
    assert (row.outcome, row.label, row.label_source) == ("COMPLETED", "GENUINE", "review_verdict")


def test_a_cancelled_payment_writes_a_cancelled_row(stack, app):
    action = held_after_checks(stack, app)
    assert app.post(f"/api/actions/{action['id']}/cancel").json()["action"]["status"] == "CANCELLED"
    assert _row(action["id"])[1].outcome == "CANCELLED"


def test_an_expired_payment_writes_an_expired_row(stack, app):
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=300).json()
    with db.session() as s:
        txn = _txn(s, started["action"]["id"])
        sess = s.get(db.AuthSession, txn.session_id)
        sess.expires_at = 0
    with db.session() as s:
        assert orchestrator.expire_stale(s) >= 1
    assert _row(started["action"]["id"])[1].outcome == "EXPIRED"


def test_with_the_switch_off_nothing_is_written(stack, app):
    _switch(stack, False)
    aid = _pay(stack, app, 2_500)
    assert _row(aid)[1] is None


def test_the_payment_goes_through_even_if_writing_the_row_fails(stack, app, monkeypatch):
    monkeypatch.setattr(learning_store, "_band_vs_usual", lambda *a: 1 / 0)
    before = balance(app)
    aid = _pay(stack, app, 2_500)
    assert balance(app) == before - 2_500_00
    assert _row(aid)[1] is None


def _decided_under(action_id):
    with db.session() as s:
        txn = _txn(s, action_id)
        d = s.execute(select(db.Decision).where(db.Decision.transaction_id == txn.id)).scalars().first()
        return d.plan.get("bandState"), d.plan.get("bandRulesAdded")


def _band_state(stack, band, state, evaluated_at=None):
    import time
    with db.session() as s:
        st = s.get(db.BandGateState, (stack.tenant["id"], band))
        if st is None:
            st = db.BandGateState(tenant_id=stack.tenant["id"], band=band, figures={}, changed_at=time.time())
            s.add(st)
        st.state, st.evaluated_at = state, evaluated_at if evaluated_at is not None else time.time()
        st.model_version = "stub"                        # the scripted model's version: a band on MODEL is approved on it


def test_a_payment_is_decided_under_its_own_bands_state_and_a_split_payment_under_the_higher_band(stack, app):
    _band_state(stack, 1, "MODEL")
    _band_state(stack, 2, "COLLECTING")
    try:
        first = _pay(stack, app, 2_500)
        assert _decided_under(first)[0] == "MODEL"
        # Rs 7,500 more on the same day: Rs 10,000 in 24 h is band 2, which is still on the rules
        second = act(app, "transfer", payeeRef="BEN-42", amountRupees=7_500).json()["action"]["id"]
        assert _decided_under(second)[0] == "COLLECTING"
    finally:
        _band_state(stack, 1, "COLLECTING")


def test_a_model_band_whose_grading_has_gone_stale_is_decided_by_the_rules(stack, app):
    import time
    _band_state(stack, 0, "MODEL", evaluated_at=time.time() - 49 * 3600)
    try:
        aid = act(app, "transfer", payeeRef="BEN-42", amountRupees=300).json()["action"]["id"]
        assert _decided_under(aid)[0] == "COLLECTING"
    finally:
        _band_state(stack, 0, "COLLECTING")


def test_new_weights_swapped_in_are_not_trusted_on_the_next_payment_or_after_the_nights_grading(stack, app, monkeypatch):
    """The band was approved on the scripted model ("stub"). Someone loads new weights directly: the very next payment in that
    band is decided with the rules as the minimum, and the night's grading sends the band back to COLLECTING."""
    from amfa.core import band_gate
    from amfa.engine.risk import scorer
    _band_state(stack, 1, "MODEL")
    try:
        before = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()["action"]["id"]
        assert _decided_under(before)[0] == "MODEL"
        swapped = scorer.Result(score=0.5, raw=0.5, level="T1", reasons=[("payee_seen", 0.2)], version="stub-2")
        monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: swapped, "version": "stub-2"})())
        after = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()["action"]["id"]
        assert _decided_under(after)[0] == "COLLECTING"
        with db.session() as s:
            band_gate.evaluate(s, stack.tenant["id"], TenantPolicy.model_validate(s.get(db.Tenant, stack.tenant["id"]).policy))
            st = s.get(db.BandGateState, (stack.tenant["id"], 1))
            assert (st.state, st.model_version) == ("COLLECTING", "stub-2")
    finally:
        _band_state(stack, 1, "COLLECTING")


def test_a_candidate_model_watches_real_payments_without_changing_them(stack, app, monkeypatch):
    from amfa.engine.risk import scorer
    first = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()["action"]
    watcher = scorer.Result(score=0.9, raw=0.9, level="T4", reasons=[], version="stub-candidate")
    monkeypatch.setattr(scorer, "candidate", lambda: type("C", (), {"score": lambda self, f: watcher, "version": "stub-candidate"})())
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()
    second = started["action"]
    assert second["checks"] == first["checks"] and second["reviewAfter"] == first["reviewAfter"]   # a T4 candidate changed nothing
    verify(stack, started["challengeUrl"])
    assert app.post(f"/api/actions/{second['id']}/complete").json()["action"]["status"] == "DONE"
    _, row = _row(second["id"])
    assert (row.candidate_version, row.model_version) == ("stub-candidate", "stub")
    # its level is what it would decide as the active model, the bank's reliefs included: make it active for the same payment
    monkeypatch.setattr(scorer, "active", lambda: type("A", (), {"score": lambda self, f: watcher, "version": "stub-candidate"})())
    monkeypatch.setattr(scorer, "candidate", lambda: None)
    third = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()
    verify(stack, third["challengeUrl"])
    app.post(f"/api/actions/{third['action']['id']}/complete")
    _, as_active = _row(third["action"]["id"])
    assert row.candidate_level == as_active.model_level and as_active.model_version == "stub-candidate"


def test_with_the_switch_off_payments_carry_no_band_state(stack, app):
    _switch(stack, False)
    aid = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()["action"]["id"]
    assert _decided_under(aid) == (None, [])


def test_the_customer_and_bank_see_no_new_field(stack, app):
    LEVEL["now"] = "T1"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()
    assert not {"band", "bandView", "learningRow"} & set(started["action"])
