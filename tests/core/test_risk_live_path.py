"""The live path: assemble() must build the same model inputs from stored transactions that training
builds from simulated ones, and must know the amount band of the money moved in the last 24 hours."""
import time
import uuid

import pytest

from amfa.core import context_builder, db, factors
from amfa.core.context_builder import TxnInput
from amfa.engine.context import Band, TxnType
from amfa.engine.policy import TenantPolicy

DAY = 86_400.0
NOW = time.time()


@pytest.fixture(scope="module")
def tenant():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="Test Bank", slug=f"t{uuid.uuid4().hex[:8]}",
                      policy=TenantPolicy().model_dump(mode="json"))
        s.add(t)
    return t


@pytest.fixture
def customer(tenant):
    """A senior customer with an 800-day-old account; every test gets its own, so none depends on another."""
    with db.session() as s:
        c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=tenant.id, external_ref=uuid.uuid4().hex[:12],
                        segment="SENIOR", created_at=NOW - 800 * DAY)
        s.add(c)
    return c


@pytest.fixture(autouse=True)
def behaviour_service(monkeypatch):
    monkeypatch.setattr(factors, "ueba", lambda path, body: {"riskScore": 0.2, "band": "LOW", "status": "OK",
                                                              "dimensionsUsed": ["transaction"], "latencyMs": 1})


def pay(s, tenant, customer, *, ago_days, amount, payee, device="dev-phone", status="AUTHORIZED", type="TRANSFER"):
    t = db.Transaction(
        id=f"txn_{uuid.uuid4().hex[:20]}", tenant_id=tenant.id, customer_id=customer.id,
        external_id=uuid.uuid4().hex[:12], idempotency_key=uuid.uuid4().hex, request_hash="x", type=type,
        amount_minor=int(amount * 100), currency="INR", payee_ref=payee, channel="MOBILE", status=status,
        facts={"payeeKey": context_builder.payee_key(tenant.id, payee),
               "deviceKey": context_builder.device_key(tenant.id, device), "network": "net-a", "placeKey": "IN:Pune"},
        created_at=NOW - ago_days * DAY)
    s.add(t)


def txn(amount, payee="landlord", **kw):
    return TxnInput(type=TxnType.TRANSFER, amount_minor=int(amount * 100), currency="INR", payee_ref=payee,
                    channel="MOBILE", device_id="dev-phone", ip="203.0.113.7", at=NOW, **kw)


def test_features_come_from_the_stored_year(tenant, customer):
    with db.session() as s:
        for ago in (363, 182):                                    # half-yearly rent, same amount
            pay(s, tenant, customer, ago_days=ago, amount=30_000, payee="landlord")
        pay(s, tenant, customer, ago_days=400, amount=30_000, payee="landlord")       # beyond the memory
        pay(s, tenant, customer, ago_days=5, amount=1_500, payee="pharmacy", status="DENIED")
        s.flush()
        a = context_builder.assemble(s, tenant, customer, "txn_new", txn(30_000))
    f = a.features
    assert f["payee_seen"] == 2 and f["days_since_payee_last_paid"] == pytest.approx(182, abs=.01)
    assert f["amount_vs_payee_avg"] == pytest.approx(1.0) and f["payee_cycle_error"] < .01
    assert f["history_count"] == 2                                # the 400-day-old and the denied ones do not count
    assert f["segment"] == 2 and f["account_age_days"] == pytest.approx(800, abs=.1)
    assert f["ueba_risk"] == 0.2 and f["sim_changed_hours"] is None


def test_account_events_and_payee_flag_reach_the_model(tenant, customer):
    events = {"simChangedAt": NOW - 3 * 3600, "payeeRiskFlag": True}
    with db.session() as s:
        built = context_builder.assemble(s, tenant, customer, "txn_new", txn(900, "stranger", account_events=events))
    f = built.features
    assert f["sim_changed_hours"] == pytest.approx(3, abs=.01) and f["payee_risk_flag"] == 1


def test_exposure_band_counts_the_last_24_hours(tenant, customer):
    with db.session() as s:
        small = context_builder.assemble(s, tenant, customer, "txn_a", txn(4_000, "newshop"))
        pay(s, tenant, customer, ago_days=.2, amount=8_000, payee="shop-b", status="DENIED")   # attempted, not sent
        s.flush()
        stacked = context_builder.assemble(s, tenant, customer, "txn_b", txn(4_000, "newshop"))
        big = context_builder.assemble(s, tenant, customer, "txn_c", txn(50_000, "newshop"))
    assert small.ctx.transaction.exposureBand is Band.LOW                 # Rs 4,000 on its own
    assert stacked.ctx.transaction.exposureBand is Band.MEDIUM            # Rs 4,000 + Rs 8,000 attempted today
    assert big.ctx.transaction.exposureBand is Band.MEDIUM and big.ctx.policy.smallPaymentCap is True


def test_behaviour_service_down_means_missing_not_safe(tenant, customer, monkeypatch):
    monkeypatch.setattr(factors, "ueba", lambda path, body: None)
    with db.session() as s:
        f = context_builder.assemble(s, tenant, customer, "txn_new", txn(900, "someone")).features
    assert f["ueba_risk"] is None


def test_factors_enrolled_counts_one_per_factor():
    from amfa.engine.context import Enrollment
    none = dict(otp=False, face=False, voice=False, keystroke=False, shape=False)
    assert context_builder._factors_enrolled(Enrollment()) == 4
    assert context_builder._factors_enrolled(Enrollment(**none | {"otp": True, "shape": True})) == 2
    assert context_builder._factors_enrolled(Enrollment(**none)) == 0
