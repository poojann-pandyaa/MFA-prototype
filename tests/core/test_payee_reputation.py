"""A payee that takes money from many customers at once, or that customers have reported, is flagged for everyone who is about to pay it."""
import time
import uuid

import pytest

from amfa.core import context_builder, db, factors, payee_reputation
from amfa.core.context_builder import TxnInput
from amfa.engine.context import TxnType
from amfa.engine.policy import TenantPolicy

DAY = 86_400.0
NOW = time.time()


def _policy(**rep):
    p = TenantPolicy().model_dump(mode="json")
    p["payeeReputation"] = {"enabled": True, **rep}
    return p


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="B", slug=f"r{uuid.uuid4().hex[:8]}", policy=_policy())
        s.add(t)
    return t


@pytest.fixture(autouse=True)
def behaviour_service(monkeypatch):
    monkeypatch.setattr(factors, "ueba", lambda path, body: {"riskScore": 0.2, "band": "LOW", "status": "OK", "dimensionsUsed": [], "latencyMs": 1})


def customers(bank, n, *, created_ago_days=400):
    out = []
    with db.session() as s:
        for _ in range(n):
            c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=bank.id, external_ref=uuid.uuid4().hex[:12], created_at=NOW - created_ago_days * DAY)
            s.add(c)
            out.append(c.id)
    return out


def senders(bank, ref, ids, first_ago_days):
    key = context_builder.payee_key(bank.id, ref)
    with db.session() as s:
        for cid in ids:
            s.add(db.KnownPayee(customer_id=cid, payee_key=key, first_seen=NOW - first_ago_days * DAY, count=1))


def report(bank, ref, reporter):
    with db.session() as s:
        tid = f"txn_{uuid.uuid4().hex[:12]}"
        s.add(db.Transaction(id=tid, tenant_id=bank.id, customer_id=reporter, external_id=tid, idempotency_key=tid, request_hash="x", type="TRANSFER",
                             amount_minor=100000, currency="INR", payee_ref=ref, channel="MOBILE", status="CONSUMED", created_at=NOW - 3 * DAY))
        s.flush()
        s.add(db.RiskLabel(tenant_id=bank.id, transaction_id=tid, label="FRAUD", source="customer_report", reported_at=NOW - DAY))


def check(bank, ref, me="cu_me", registry=None):
    with db.session() as s:
        return payee_reputation.evaluate(s, bank.id, TenantPolicy.model_validate(_policy()), me, ref, context_builder.payee_key(bank.id, ref), NOW, registry)


def test_a_new_payee_that_many_customers_paid_this_week_is_flagged_for_the_next_one(bank):
    senders(bank, "MULE-1", customers(bank, 6), first_ago_days=3)
    r = check(bank, "MULE-1")
    assert r["flag"] and r["senders7d"] == 6 and "6 different customers" in r["reasons"][0]


def test_a_few_senders_or_old_senders_are_nothing(bank):
    senders(bank, "QUIET", customers(bank, 3), first_ago_days=2)
    senders(bank, "SLOW", customers(bank, 9), first_ago_days=40)           # many, but over weeks: not a sudden fan-in
    assert not check(bank, "QUIET")["flag"] and not check(bank, "SLOW")["flag"]


def test_a_shop_or_utility_is_never_flagged_however_many_pay_it(bank):
    senders(bank, "SHOP", customers(bank, 50), first_ago_days=1)
    with db.session() as s:                                                  # the first of them paid it 200 days ago
        key = context_builder.payee_key(bank.id, "SHOP")
        s.add(db.KnownPayee(customer_id=customers(bank, 1)[0], payee_key=key, first_seen=NOW - 200 * DAY, count=40))
    assert not check(bank, "SHOP")["flag"]
    senders(bank, "UTILITY", customers(bank, 30), first_ago_days=2)
    assert not check(bank, "UTILITY", registry=NOW - 300 * DAY)["flag"]      # the bank's own registry says it is established


def test_a_burst_in_one_day_flags_a_payee_that_is_not_yet_established(bank):
    senders(bank, "BURST", customers(bank, 11), first_ago_days=0.5)
    r = check(bank, "BURST")
    assert r["flag"] and r["senders24h"] == 11


def test_one_fraud_report_by_another_customer_flags_the_payee_for_everyone_else(bank):
    ids = customers(bank, 2)
    report(bank, "SCAMMER", ids[0])
    r = check(bank, "SCAMMER", me=ids[1])
    assert r["flag"] and r["reports"] == 1 and "reported as fraud" in r["reasons"][0]
    assert not check(bank, "SCAMMER", me=ids[0])["flag"]                    # the reporter is not told about their own report


def test_a_report_flags_even_an_old_payee(bank):
    senders(bank, "OLD", customers(bank, 40), first_ago_days=300)
    report(bank, "OLD", customers(bank, 1)[0])
    assert check(bank, "OLD")["flag"]


def test_it_reaches_the_risk_model_as_a_risk_listed_payee_and_is_off_unless_the_bank_turns_it_on(bank):
    senders(bank, "MULE-2", customers(bank, 6), first_ago_days=3)
    cid = customers(bank, 1)[0]
    t = TxnInput(type=TxnType.TRANSFER, amount_minor=5_000_00, currency="INR", payee_ref="MULE-2", channel="MOBILE", device_id="dev-phone",
                 ip="203.0.113.7", at=NOW)
    with db.session() as s:
        on = context_builder.assemble(s, s.get(db.Tenant, bank.id), s.get(db.Customer, cid), "txn_x", t)
        assert on.features["payee_risk_flag"] == 1 and on.derived["payeeReputation"]["flag"]
        tenant = s.get(db.Tenant, bank.id)
        tenant.policy = {**tenant.policy, "payeeReputation": {"enabled": False}}
        s.flush()
        off = context_builder.assemble(s, tenant, s.get(db.Customer, cid), "txn_y", t)
        assert off.features["payee_risk_flag"] == 0 and "payeeReputation" not in off.derived
        s.rollback()


def test_the_customer_is_told_in_plain_words_and_never_the_detail():
    from amfa.engine.context import Plan
    from amfa.engine.rationale import customer_notice
    n = customer_notice(Plan(riskReasons=[("payee_risk_flag", 1.0)]), TxnType.TRANSFER, {"payee_risk_flag": 1})
    assert "drawn warnings from other customers" in n["reason"] and "mule" not in n["reason"].lower()
