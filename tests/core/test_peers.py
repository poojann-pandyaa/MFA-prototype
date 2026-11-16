"""What a customer says at sign-up, and how customers who answered the same way really pay (amfa/core/peers.py)."""
import math
import time
import uuid

import pytest

from amfa.core import context_builder, db, factors, peers, phase
from amfa.core.context_builder import TxnInput
from amfa.engine.context import TxnType
from amfa.engine.policy import TenantPolicy
from amfa.engine.risk import declared

DAY = 86_400.0
NOW = time.time()


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="B", slug=f"p{uuid.uuid4().hex[:8]}", policy=TenantPolicy().model_dump(mode="json"))
        s.add(t)
    return t


@pytest.fixture(autouse=True)
def behaviour_service(monkeypatch):
    monkeypatch.setattr(factors, "ueba", lambda path, body: {"riskScore": 0.2, "band": "LOW", "status": "OK", "dimensionsUsed": [], "latencyMs": 1})


def customer(bank, **kw):
    with db.session() as s:
        c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=bank.id, external_ref=uuid.uuid4().hex[:12], created_at=NOW - 400 * DAY, **kw)
        s.add(c)
    return c.id


def txn(amount=1_200, payee="shop"):
    return TxnInput(type=TxnType.TRANSFER, amount_minor=int(amount * 100), currency="INR", payee_ref=payee, channel="MOBILE",
                    device_id="dev-phone", ip="203.0.113.7", at=NOW)


def test_the_answers_are_kept_as_bands_and_can_be_taken_back(bank):
    cid = customer(bank)
    with db.session() as s:
        row = peers.save(s, s.get(db.Customer, cid), month_band=2, typical_band=1,
                         regular_payee_keys=["k1", "k2", "k2", "k3", "k4"])
        assert row.typical_band == 1 and row.regular_payees == ["k1", "k2", "k3"]       # no duplicates, at most three
        assert peers.view(row)["declared"] and peers.view(row)["regularPayees"] == 3
    with db.session() as s:
        peers.clear(s, cid)
        assert not peers.view(s.get(db.CustomerProfile, cid))["declared"]


def test_the_prior_is_what_they_said_until_enough_of_their_peers_are_known(bank):
    cid = customer(bank)
    with db.session() as s:
        row = peers.save(s, s.get(db.Customer, cid), month_band=1, typical_band=2, regular_payee_keys=[])
        assert peers.prior(s, bank.id, row) == declared.peer_prior(5_000.0)
        assert peers.prior(s, bank.id, None) is None
        skipped = peers.save(s, s.get(db.Customer, cid), month_band=None, typical_band=None, regular_payee_keys=[])
        assert peers.prior(s, bank.id, skipped) is None                                  # skipped the question: no prior


def _ready_peers(bank, n, band, amount):
    ids = []
    with db.session() as s:
        for i in range(n):
            c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=bank.id, external_ref=uuid.uuid4().hex[:12], created_at=NOW - 300 * DAY)
            s.add(c)
            s.flush()
            s.add(db.CustomerProfile(customer_id=c.id, typical_band=band, month_band=2, regular_payees=[]))
            s.add(db.CustomerPhase(customer_id=c.id, phase=phase.READY, evidence={}, verified_payments=30))
            for k in range(3):
                x = f"txn_{uuid.uuid4().hex[:12]}"
                s.add(db.Transaction(id=x, tenant_id=bank.id, customer_id=c.id, external_id=x, idempotency_key=x, request_hash="x", type="TRANSFER",
                                     amount_minor=int(amount * 100 * (1 + .1 * k)), currency="INR", payee_ref="p", channel="MOBILE", status="CONSUMED",
                                     created_at=NOW - (k + 1) * 10 * DAY))
            ids.append(c.id)
    return ids


def test_enough_ready_peers_replace_the_declared_figure_with_how_they_really_pay(bank):
    _ready_peers(bank, 60, band=1, amount=2_400.0)                                       # they said about Rs 1,200 and really pay about Rs 2,500
    with db.session() as s:
        assert peers.refresh(s, NOW) == 1
        stat = s.query(db.PeerGroupStat).filter_by(tenant_id=bank.id).one()
        assert stat.n_customers == 60 and stat.centre_log == pytest.approx(math.log1p(2_520), abs=.15)
    cid = customer(bank)
    with db.session() as s:
        row = peers.save(s, s.get(db.Customer, cid), month_band=1, typical_band=1, regular_payee_keys=[])
        centre, spread = peers.prior(s, bank.id, row)
        assert centre == pytest.approx(math.log1p(2_520), abs=.15) and spread >= .3          # not the declared 1,200


def test_a_group_with_too_few_ready_customers_is_never_used_so_nobody_can_be_picked_out(bank):
    _ready_peers(bank, 12, band=3, amount=30_000.0)
    with db.session() as s:
        peers.refresh(s, NOW)                                                                    # (it runs over every bank at once)
        assert s.query(db.PeerGroupStat).filter_by(tenant_id=bank.id).count() == 0
        row = peers.save(s, s.get(db.Customer, customer(bank)), month_band=3, typical_band=3, regular_payee_keys=[])
        assert peers.prior(s, bank.id, row) == declared.peer_prior(25_000.0)


def test_the_declaration_reaches_the_model_inputs_from_the_first_payment(bank):
    cid = customer(bank)
    with db.session() as s:
        peers.save(s, s.get(db.Customer, cid), month_band=0, typical_band=0, regular_payee_keys=[])
        big = context_builder.assemble(s, s.get(db.Tenant, bank.id), s.get(db.Customer, cid), "t1", txn(40_000)).features
        small = context_builder.assemble(s, s.get(db.Tenant, bank.id), s.get(db.Customer, cid), "t2", txn(300)).features
        s.rollback()
    assert big["habit_surprise"] > 4 and small["habit_surprise"] == 0
    assert big["amount_vs_declared"] == pytest.approx(40_000 / 300) and big["declared_gap"] == pytest.approx(40_000 / 5_000)


def test_a_payee_the_bank_vouched_for_at_sign_up_is_not_a_stranger_but_never_lowers_a_warning(bank):
    cid = customer(bank)
    key = context_builder.payee_key(bank.id, "RENT-LANDLORD")
    with db.session() as s:
        stranger = context_builder.assemble(s, s.get(db.Tenant, bank.id), s.get(db.Customer, cid), "t1", txn(8_000, "RENT-LANDLORD"))
        peers.save(s, s.get(db.Customer, cid), month_band=1, typical_band=2, regular_payee_keys=[key])
        known = context_builder.assemble(s, s.get(db.Tenant, bank.id), s.get(db.Customer, cid), "t2", txn(8_000, "RENT-LANDLORD"))
        other = context_builder.assemble(s, s.get(db.Tenant, bank.id), s.get(db.Customer, cid), "t3", txn(8_000, "SOMEONE-ELSE"))
        s.rollback()
    assert stranger.ctx.transaction.isNewBeneficiary and not known.ctx.transaction.isNewBeneficiary and other.ctx.transaction.isNewBeneficiary
    assert known.derived["payee"]["basis"] == "declared at sign-up"
