"""#3 end to end: payments made through the Demo Bank against a real in-process core whose band switch runs as its own service (its
own database, signed calls). Rows land only in the service's database; a payment is decided under the state the service holds;
with the service down every payment still goes through, under the stricter plan, and its row arrives later, once."""
import importlib
import os
import sys
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from amfa import config
from amfa.bands import db as bdb
from amfa.bands import remote
from amfa.core import api_admin, db, learning_store
from amfa.core.app import create_app
from amfa.core.auth import DEFAULT_SCOPES
from amfa.engine.policy import TenantPolicy
from tests.core import bands_remote as R
# the customer, check helpers and scripted services of the customer-app tests
from tests.core.test_customer_app import LEVEL, _txn, act, app, balance, fresh_rate_limits, held_after_checks, scripted, verify  # noqa: F401


@pytest.fixture(scope="module")
def stack():
    mp = pytest.MonkeyPatch()
    service_client = R.start(mp)
    base = config.get().public_url
    try:
        with TestClient(create_app(), base_url=base) as core:
            tenant = api_admin.create_tenant("Remote Band Bank", f"rband{uuid.uuid4().hex[:6]}", "test")
            key = api_admin.create_api_key(tenant["id"], "bank", list(DEFAULT_SCOPES), "test")
            saved = {k: os.environ.get(k) for k in ("AMFA_BASE_URL", "AMFA_KEY_ID", "AMFA_KEY_SECRET", "AMFA_TENANT_ID",
                                                    "BANK_SESSION_SECRET")}
            os.environ.update(AMFA_BASE_URL=base, AMFA_KEY_ID=key["keyId"], AMFA_KEY_SECRET=key["secret"],
                              AMFA_TENANT_ID=tenant["id"], BANK_SESSION_SECRET="test-session-secret")
            os.environ.pop("BANK_STATE_FILE", None)
            first_import = "examples.demo_bank.bank" not in sys.modules     # the Demo Bank reads its tenant once, at import
            bank = importlib.import_module("examples.demo_bank.bank")
            old = (bank.AMFA, bank.TENANT)
            bank.AMFA, bank.TENANT = bank.AmfaClient(base, key["keyId"], key["secret"], http=core), tenant["id"]
            try:
                yield SimpleNamespace(core=core, bank=bank, tenant=tenant, service=service_client)
            finally:
                bank.AMFA, bank.TENANT = old
                if first_import:                       # so the next test module that imports it gets its own bank, not this one
                    sys.modules.pop("examples.demo_bank.bank", None)
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
    finally:
        R.stop()
        mp.undo()


def _switch(stack, on: bool):
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        p = TenantPolicy.model_validate(t.policy).model_dump(mode="json")
        p["collection"]["enabled"] = on
        t.policy = p


@pytest.fixture(autouse=True)
def switch_on(stack):
    _switch(stack, True)
    remote.mount(stack.service)
    with db.session() as s:
        for it in s.execute(select(db.BandOutbox)).scalars():
            s.delete(it)
    yield
    remote.mount(stack.service)
    _switch(stack, False)


def _link(action_id):
    with db.session() as s:
        return learning_store.link_of(_txn(s, action_id).id)


def _band_row(action_id):
    with bdb.session() as bs:
        return bs.execute(select(db.LearningRow).where(db.LearningRow.link == _link(action_id))).scalars().first()


def _core_row(action_id):
    with db.session() as s:
        return s.execute(select(db.LearningRow).where(db.LearningRow.link == _link(action_id))).scalars().first()


def _pay(stack, app, rupees, payee="BEN-42"):
    started = act(app, "transfer", payeeRef=payee, amountRupees=rupees).json()
    verify(stack, started["challengeUrl"])
    done = app.post(f"/api/actions/{started['action']['id']}/complete").json()
    assert done["action"]["status"] == "DONE"
    return started["action"]["id"]


def _decided_under(action_id):
    with db.session() as s:
        d = s.execute(select(db.Decision).where(db.Decision.transaction_id == _txn(s, action_id).id)).scalars().first()
        return d.plan.get("bandState")


def _band_state(stack, band, state):
    with bdb.session() as bs:
        st = bs.get(db.BandGateState, (stack.tenant["id"], band)) or db.BandGateState(tenant_id=stack.tenant["id"], band=band,
                                                                                       figures={}, changed_at=time.time())
        st.state, st.evaluated_at, st.model_version, st.streak = state, time.time(), "stub", 4
        bs.merge(st)


def test_a_completed_payment_writes_its_row_only_in_the_band_services_database(stack, app):
    aid = _pay(stack, app, 2_500)
    row = _band_row(aid)
    assert row is not None and (row.band, row.outcome, row.label, row.mode, row.model_version) == (1, "COMPLETED", "UNKNOWN",
                                                                                                     "COLLECTING", "stub")
    assert _core_row(aid) is None
    with db.session() as s:
        assert s.execute(select(func.count()).select_from(db.LearningRow)
                         .where(db.LearningRow.tenant_id == stack.tenant["id"])).scalar_one() == 0


def test_this_was_not_me_labels_the_row_in_the_band_service(stack, app):
    aid = _pay(stack, app, 500)
    assert app.post(f"/api/actions/{aid}/report", json={"reason": "not me"}).json()["action"]["reported"]
    row = _band_row(aid)
    assert (row.label, row.label_source) == ("FRAUD", "customer_report")


def test_a_cancelled_payment_writes_a_cancelled_row_in_the_band_service(stack, app):
    action = held_after_checks(stack, app)
    assert app.post(f"/api/actions/{action['id']}/cancel").json()["action"]["status"] == "CANCELLED"
    assert _band_row(action["id"]).outcome == "CANCELLED"


def test_a_payment_is_decided_under_the_state_the_band_service_holds(stack, app):
    _band_state(stack, 1, "MODEL")
    try:
        aid = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()["action"]["id"]
        assert _decided_under(aid) == "MODEL"
    finally:
        _band_state(stack, 1, "COLLECTING")


@pytest.mark.parametrize("outage", [R.Down(), R.Down(httpx.ReadTimeout("slow")), R.Answers(503, {"error": "busy"})],
                         ids=["down", "slow", "503"])
def test_with_the_service_out_a_payment_still_goes_through_under_the_stricter_plan_and_its_row_arrives_later_once(stack, app,
                                                                                                                   outage):
    _band_state(stack, 0, "MODEL")
    try:
        LEVEL["now"] = "T1"
        up = act(app, "transfer", payeeRef="BEN-42", amountRupees=300).json()
        assert _decided_under(up["action"]["id"]) == "MODEL"
        remote.mount(outage)
        before = balance(app)
        started = act(app, "transfer", payeeRef="BEN-42", amountRupees=300).json()
        aid = started["action"]["id"]
        assert _decided_under(aid) == "COLLECTING"
        assert set(up["action"]["checks"]) <= set(started["action"]["checks"])       # never fewer checks than with the service up
        verify(stack, started["challengeUrl"])
        assert app.post(f"/api/actions/{aid}/complete").json()["action"]["status"] == "DONE"
        assert balance(app) == before - 300_00
        assert _band_row(aid) is None and _core_row(aid) is None
        with db.session() as s:
            assert [h.kind for h in s.execute(select(db.BandOutbox)).scalars()] == ["ROW"]
        remote.mount(stack.service)
        with db.session() as s:
            assert remote.flush_outbox(s)["sent"] == 1
        with db.session() as s:
            assert remote.flush_outbox(s)["sent"] == 0
        assert _band_row(aid).outcome == "COMPLETED"
    finally:
        remote.mount(stack.service)
        _band_state(stack, 0, "COLLECTING")


def test_with_the_switch_off_nothing_reaches_the_band_service(stack, app):
    _switch(stack, False)
    calls = []

    class Spy:
        def post(self, path, **kw):
            calls.append(path)
            return stack.service.post(path, **kw)
    remote.mount(Spy())
    aid = _pay(stack, app, 2_500)
    assert _band_row(aid) is None and calls == []


def test_the_customer_and_bank_see_no_new_field(stack, app):
    LEVEL["now"] = "T1"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_500).json()
    assert not {"band", "bandView", "learningRow", "bandState"} & set(started["action"])
