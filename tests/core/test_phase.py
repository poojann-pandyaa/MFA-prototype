"""Learning or ready, kept per customer from their own verified payments (amfa/core/phase.py)."""
import time
import uuid

from amfa.core import db, phase
from amfa.engine.policy import TenantPolicy

DAY = 86_400.0


def _setup(policy_learning=True):
    db.migrate()
    pol = TenantPolicy().model_dump(mode="json")
    pol["learning"] = {"enabled": policy_learning}
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="B", slug=f"p{uuid.uuid4().hex[:8]}", policy=pol)
        s.add(t)
        s.flush()
        c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=t.id, external_ref=uuid.uuid4().hex[:12])
        s.add(c)
        s.flush()
        return t.id, c.id, TenantPolicy.model_validate(pol)


def _txn(s, tid, cid, ago_days, amount=1_500, status="CONSUMED"):
    x = f"txn_{uuid.uuid4().hex[:12]}"
    t = db.Transaction(id=x, tenant_id=tid, customer_id=cid, external_id=x, idempotency_key=x, request_hash="x", type="TRANSFER",
                       amount_minor=amount * 100, currency="INR", payee_ref="p", channel="MOBILE", status=status,
                       created_at=time.time() - ago_days * DAY)
    s.add(t)
    s.flush()
    return t


def test_a_customer_with_no_history_starts_learning_and_a_bank_that_has_not_switched_it_on_sees_everyone_ready():
    tid, cid, pol = _setup()
    with db.session() as s:
        c = s.get(db.Customer, cid)
        assert phase.current(s, c, pol) == phase.LEARNING
        off = TenantPolicy.model_validate({**pol.model_dump(mode="json"), "learning": {"enabled": False}})
        assert phase.current(s, c, off) == phase.READY


def test_payments_the_model_would_not_have_alarmed_on_make_a_customer_ready_after_enough_separate_days():
    tid, cid, pol = _setup()
    with db.session() as s:
        c = s.get(db.Customer, cid)
        seen = []
        for i in range(16):
            t = _txn(s, tid, cid, ago_days=40 - i * 2, amount=1_500 + (i % 3) * 50)
            row = phase.observe(s, c, t, would_have_alarmed=False)
            seen.append(row.phase)
        assert seen[0] == phase.LEARNING and seen[-1] == phase.READY
        assert row.ready_since is not None and row.verified_payments == 16


def test_false_alarms_on_their_own_payments_keep_a_customer_learning():
    tid, cid, pol = _setup()
    with db.session() as s:
        c = s.get(db.Customer, cid)
        for i in range(24):
            t = _txn(s, tid, cid, ago_days=48 - i * 2)
            row = phase.observe(s, c, t, would_have_alarmed=(i % 3 == 0))
        assert row.phase == phase.LEARNING
        assert "proven" in phase.view(s, c, pol)["progress"]["missing"]


def test_a_fraud_report_sends_a_ready_customer_back_to_learning_and_forgets_what_it_thought_it_knew():
    tid, cid, pol = _setup()
    with db.session() as s:
        c = s.get(db.Customer, cid)
        for i in range(16):
            row = phase.observe(s, c, _txn(s, tid, cid, ago_days=40 - i * 2), would_have_alarmed=False)
        assert row.phase == phase.READY
        phase.reset(s, cid)
        assert row.phase == phase.LEARNING and row.ready_since is None and row.verified_payments == 0
        assert phase.view(s, c, pol)["progress"]["payments"] == 0


def test_a_customer_who_existed_before_it_was_switched_on_is_read_from_their_history():
    tid, cid, pol = _setup()
    with db.session() as s:
        for i in range(20):
            _txn(s, tid, cid, ago_days=60 - i * 3)
        assert phase.current(s, s.get(db.Customer, cid), pol) == phase.READY
    tid2, cid2, pol2 = _setup()
    with db.session() as s:
        for i in range(3):
            _txn(s, tid2, cid2, ago_days=5 - i)
        assert phase.current(s, s.get(db.Customer, cid2), pol2) == phase.LEARNING


def test_only_payments_teach_and_progress_says_what_is_left():
    tid, cid, pol = _setup()
    with db.session() as s:
        c = s.get(db.Customer, cid)
        x = _txn(s, tid, cid, ago_days=1)
        x.type = "ADD_BENEFICIARY"
        phase.observe(s, c, x, would_have_alarmed=False)
        v = phase.view(s, c, pol)
        assert v["phase"] == "LEARNING" and v["progress"]["payments"] == 0 and v["progress"]["moreAtLeast"] > 0
        assert v["offerBiometrics"] is False
