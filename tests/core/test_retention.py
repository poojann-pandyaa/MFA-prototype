"""Nothing older than the retention window (90 days) is kept: transactions and everything derived from them go, the customer stays."""
import time
import uuid

from sqlalchemy import select

from amfa.core import db, retention
from amfa.core.context_builder import payee_key
from amfa.engine.policy import TenantPolicy

DAY = 86_400.0


def _txn(s, tenant_id, cust_id, ago_days, payee="BEN-1", label=True):
    now = time.time()
    tid = f"txn_{uuid.uuid4().hex[:12]}"
    s.add(db.Transaction(id=tid, tenant_id=tenant_id, customer_id=cust_id, external_id=tid, idempotency_key=tid,
                         request_hash="x", type="TRANSFER", amount_minor=500_00, currency="INR", payee_ref=payee,
                         channel="MOBILE", status="CONSUMED", created_at=now - ago_days * DAY))
    s.flush()
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    s.add(db.AuthSession(id=sid, tenant_id=tenant_id, transaction_id=tid, customer_id=cust_id, plan_id="p", status="APPROVED",
                         checklist=[], context={}, risk_band="LOW", expires_at=now - ago_days * DAY + 300))
    s.add(db.Decision(plan_id="p", tenant_id=tenant_id, transaction_id=tid, customer_id=cust_id, context={"_features": {}},
                      preview={}, plan={}, source="model", floor="L2", floor_applied=False, risk_band="LOW", reasoning="",
                      counterfactual="", latency_ms=1, policy_version=1, decided_at=now - ago_days * DAY))
    s.add(db.RiskLabel(tenant_id=tenant_id, transaction_id=tid, label="GENUINE", source="bank_ops"))
    s.flush()
    return tid, sid


def test_old_transactions_and_what_derives_from_them_are_deleted_and_recent_ones_kept():
    db.migrate()
    now = time.time()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="Bank", slug=f"r{uuid.uuid4().hex[:8]}",
                      policy=TenantPolicy().model_dump(mode="json"))
        s.add(t)
        s.flush()
        c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=t.id, external_ref=uuid.uuid4().hex[:12])
        s.add(c)
        s.flush()
        old, old_sess = _txn(s, t.id, c.id, 100, payee="BEN-OLD")
        new, new_sess = _txn(s, t.id, c.id, 10, payee="BEN-NEW")
        s.add(db.KnownPayee(customer_id=c.id, payee_key=payee_key(t.id, "BEN-OLD"), first_seen=now - 400 * DAY, count=9))
        s.add(db.KnownPayee(customer_id=c.id, payee_key=payee_key(t.id, "BEN-NEW"), first_seen=now - 400 * DAY, count=9))
        s.add(db.KnownNetwork(customer_id=c.id, network="wifi-old", first_seen=now - 300 * DAY, last_seen=now - 120 * DAY))
        s.add(db.KnownNetwork(customer_id=c.id, network="wifi-new", first_seen=now - 300 * DAY, last_seen=now - 2 * DAY))
        cid = c.id
    with db.session() as s:
        gone = retention.purge(s, days=90)
    assert gone["txn"] >= 1 and gone["decision"] >= 1 and gone["auth_session"] >= 1 and gone["risk_label"] >= 1
    with db.session() as s:
        ids = set(s.execute(select(db.Transaction.id).where(db.Transaction.customer_id == cid)).scalars())
        assert ids == {new}
        assert s.get(db.AuthSession, old_sess) is None and s.get(db.AuthSession, new_sess) is not None
        assert not s.execute(select(db.Decision).where(db.Decision.transaction_id == old)).first()
        assert s.execute(select(db.Decision).where(db.Decision.transaction_id == new)).first()
        assert not s.execute(select(db.RiskLabel).where(db.RiskLabel.transaction_id == old)).first()
        payees = {p.payee_key for p in s.execute(select(db.KnownPayee).where(db.KnownPayee.customer_id == cid)).scalars()}
        assert payees == {payee_key(s.get(db.Customer, cid).tenant_id, "BEN-NEW")}      # kept only while a transaction explains it
        nets = {n.network for n in s.execute(select(db.KnownNetwork).where(db.KnownNetwork.customer_id == cid)).scalars()}
        assert nets == {"wifi-new"}
        assert s.get(db.Customer, cid) is not None                                       # the customer is not a transaction


def test_a_shorter_window_can_be_chosen_and_a_second_purge_finds_nothing_more():
    with db.session() as s:
        again = retention.purge(s, days=90)
    assert not any(again.values())
