"""Phase 2 of the band switch: the anonymous learning store. Privacy first: what a row may hold, that nothing in it points at a person,
that the label link expires, and that no one can flood it."""
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect, select

from amfa.core import db, learning_store, retention
from amfa.engine.policy import TenantPolicy

DAY = 86_400.0
IST = ZoneInfo("Asia/Kolkata")
# a Wednesday, 14:30 in India
NOON = datetime(2026, 11, 18, 14, 30, tzinfo=IST).timestamp()

ALLOWED = {"id", "tenant_id", "band", "band_vs_usual", "payee_kind", "device_kind", "hour_bucket", "hard_sign", "rules_level",
           "model_level", "model_version", "candidate_level", "candidate_version", "mode", "outcome", "label", "label_source", "week_start", "link", "link_expires_at"}
BANNED = ("customer", "user", "account", "amount", "minor", "rupee", "payee_ref", "payee_display", "payee_key", "device_key",
          "device_id", "network", "place", "city", "country", "ip", "address", "phone", "email", "name", "created", "updated",
          "decided", "settled", "timestamp", "txn", "transaction", "session", "external")
VIEW = {"band": 1, "rulesLevel": "T1", "modelLevel": "T0", "payeeKind": "KNOWN", "deviceKind": "KNOWN", "hardSign": False}


def _policy(enabled=True, **collection):
    p = TenantPolicy().model_dump(mode="json")
    p["collection"] = {"enabled": enabled, **collection}
    return p


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="B", slug=f"l{uuid.uuid4().hex[:8]}", policy=_policy())
        c = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=t.id, external_ref=f"ref{uuid.uuid4().hex[:10]}")
        s.add(t)
        s.flush()
        s.add(c)
    return t.id, c.id


def _txn(s, tenant_id, cust_id, *, at=NOON, amount=2_500_00, status="AUTHORIZED", type="TRANSFER", view=VIEW, payee="BEN-SECRET-77"):
    tid = f"txn_{uuid.uuid4().hex[:12]}"
    s.add(db.Transaction(id=tid, tenant_id=tenant_id, customer_id=cust_id, external_id=f"ext-{tid}", idempotency_key=tid,
                         request_hash="x", type=type, amount_minor=amount, currency="INR", payee_ref=payee,
                         payee_display="Mr Secret Payee", channel="MOBILE", status=status, created_at=at))
    s.flush()
    s.add(db.Decision(plan_id="p", tenant_id=tenant_id, transaction_id=tid, customer_id=cust_id, context={}, preview={},
                      plan={"bandView": view} if view is not None else {}, source="model", floor="L2", floor_applied=False,
                      risk_band="LOW", reasoning="", counterfactual="", latency_ms=1, policy_version=1, decided_at=at))
    s.flush()
    return s.get(db.Transaction, tid)


def _row(s, txn_id):
    return s.execute(select(db.LearningRow).where(db.LearningRow.link == learning_store.link_of(txn_id))).scalars().first()


def _rows(s, tenant_id):
    return s.execute(select(db.LearningRow).where(db.LearningRow.tenant_id == tenant_id)).scalars().all()


# ---- privacy ---------------------------------------------------------------------------------------------------------------

def test_the_table_holds_exactly_the_allowed_columns():
    db.migrate()
    cols = {c["name"] for c in inspect(db.engine()).get_columns("learning_row")}
    assert cols == ALLOWED, "a column was added to learning_row: check it identifies no one, then update ALLOWED on purpose"


def test_no_column_name_suggests_anything_identifying():
    for col in ALLOWED - {"id", "tenant_id", "link", "link_expires_at"}:
        assert not any(b in col for b in BANNED), col


def test_a_written_row_contains_nothing_of_the_customer_the_payment_or_the_payee(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id, amount=7_777_77)
        row = learning_store.record(s, txn, now=NOON)
        assert row is not None
        values = " ".join(str(getattr(row, c)) for c in ALLOWED)
        cust = s.get(db.Customer, cust_id)
        for secret in (cust_id, cust.external_ref, txn.id, txn.external_id, txn.payee_ref, txn.payee_display, "777777", "7777.77",
                       "7,777", str(int(txn.created_at)), "14:30"):
            assert secret not in values, secret
        assert row.link != txn.id and len(row.link) == 64


def test_two_payments_by_one_customer_share_nothing_but_categories(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        a = learning_store.record(s, _txn(s, tenant_id, cust_id), now=NOON)
        b = learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON + 60), now=NOON)
        assert a.id != b.id and a.link != b.link
        # the ids are random, so the order rows were written in says nothing
        assert len({a.id, b.id}) == 2 and all(len(r.id) == 32 for r in (a, b))


def test_the_time_is_kept_only_as_a_part_of_the_day_and_a_week(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        row = learning_store.record(s, _txn(s, tenant_id, cust_id), now=NOON)
        assert (row.hour_bucket, row.week_start) == ("AFTERNOON", "2026-11-16")   # the Monday of that week
        assert row.link_expires_at % DAY == 0                                      # a whole day, not the settle time


@pytest.mark.parametrize("hour, bucket", [(0, "NIGHT"), (5, "NIGHT"), (6, "MORNING"), (11, "MORNING"), (12, "AFTERNOON"),
                                          (17, "AFTERNOON"), (18, "EVENING"), (23, "EVENING")])
def test_hour_buckets(hour, bucket):
    assert learning_store.hour_bucket(hour) == bucket


# ---- the label link expires --------------------------------------------------------------------------------------------------

def test_the_link_is_wiped_after_the_retention_window_and_a_late_label_reaches_nothing(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id)
        row = learning_store.record(s, txn, now=NOON)
        rid, tid = row.id, txn.id
    with db.session() as s:
        assert learning_store.apply_label(s, tid, "FRAUD", "bank_ops", now=NOON + 89 * DAY)      # inside the window: lands
    with db.session() as s:
        learning_store.purge(s, now=NOON + 92 * DAY)
    with db.session() as s:
        row = s.get(db.LearningRow, rid)
        assert row.link is None and row.link_expires_at is None
        assert row.label == "FRAUD"                                                                # what was learned stays
        assert _row(s, tid) is None
        assert not learning_store.apply_label(s, tid, "GENUINE", "bank_ops", now=NOON + 92 * DAY)


def test_a_label_after_the_link_expired_but_before_the_sweep_is_still_refused(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id)
        learning_store.record(s, txn, now=NOON)
        assert not learning_store.apply_label(s, txn.id, "FRAUD", "bank_ops", now=NOON + 95 * DAY)
        assert _row(s, txn.id).label == "UNKNOWN"


def test_the_retention_sweep_wipes_links_and_drops_rows_past_the_banks_retention(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        old = learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON - 400 * DAY), now=NOON - 400 * DAY)
        new = learning_store.record(s, _txn(s, tenant_id, cust_id), now=NOON)
        old_id, new_id = old.id, new.id
    with db.session() as s:
        out = retention.purge(s, now=NOON + DAY, days=90)
        assert out["learning_row"] >= 1 and out["learning_link"] >= 1
        assert s.get(db.LearningRow, old_id) is None
        kept = s.get(db.LearningRow, new_id)
        assert kept is not None and kept.link is not None                    # 1 day old: link still live


def test_the_link_is_wiped_even_if_the_bank_later_turns_the_switch_off(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        row = learning_store.record(s, _txn(s, tenant_id, cust_id), now=NOON)
        rid = row.id
        s.get(db.Tenant, tenant_id).policy = _policy(enabled=False)
    with db.session() as s:
        learning_store.purge(s, now=NOON + 92 * DAY)
    with db.session() as s:
        assert s.get(db.LearningRow, rid).link is None


# ---- flooding ----------------------------------------------------------------------------------------------------------------

def test_only_the_first_ten_payments_of_a_customers_day_become_rows(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        made = [learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON + i), now=NOON) for i in range(11)]
        assert sum(r is not None for r in made) == 10 and made[-1] is None
        # the next day counts afresh
        assert learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON + DAY), now=NOON) is not None


def test_another_customers_payments_do_not_use_up_the_cap(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        other = db.Customer(id=f"cu_{uuid.uuid4().hex[:12]}", tenant_id=tenant_id, external_ref=f"ref{uuid.uuid4().hex[:10]}")
        s.add(other)
        s.flush()
        for i in range(10):
            learning_store.record(s, _txn(s, tenant_id, other.id, at=NOON + i), now=NOON)
        assert learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON + 20), now=NOON) is not None


def test_the_cap_is_a_bank_setting(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        s.get(db.Tenant, tenant_id).policy = _policy(dailyRowsPerCustomer=2)
        s.flush()
        made = [learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON + i), now=NOON) for i in range(3)]
        assert [r is not None for r in made] == [True, True, False]


# ---- labels ------------------------------------------------------------------------------------------------------------------

def test_a_fraud_report_labels_the_right_row(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        a, b = _txn(s, tenant_id, cust_id), _txn(s, tenant_id, cust_id, at=NOON + 5)
        learning_store.record(s, a, now=NOON)
        learning_store.record(s, b, now=NOON)
        assert learning_store.apply_label(s, b.id, "FRAUD", "chargeback", now=NOON)
        assert (_row(s, a.id).label, _row(s, b.id).label, _row(s, b.id).label_source) == ("UNKNOWN", "FRAUD", "chargeback")


@pytest.mark.parametrize("label, source", [("FRAUD", "customer_app"), ("FRAUD", "simulator"), ("FRAUD", ""), ("DISPUTED", "bank_ops"),
                                           ("fraud", "bank_ops"), ("NOT_SURE", "review_verdict")])
def test_a_label_from_an_unknown_source_or_of_an_unknown_kind_is_ignored(bank, label, source):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id)
        learning_store.record(s, txn, now=NOON)
        assert not learning_store.apply_label(s, txn.id, label, source, now=NOON)
        assert _row(s, txn.id).label == "UNKNOWN"


def test_fraud_sticks_and_a_later_genuine_cannot_clear_it(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id)
        learning_store.record(s, txn, now=NOON)
        assert learning_store.apply_label(s, txn.id, "GENUINE", "bank_ops", now=NOON)
        assert learning_store.apply_label(s, txn.id, "FRAUD", "customer_report", now=NOON)
        assert not learning_store.apply_label(s, txn.id, "GENUINE", "bank_ops", now=NOON)
        assert _row(s, txn.id).label == "FRAUD"


def test_a_label_that_arrived_before_the_payment_settled_is_picked_up(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id)
        s.add(db.RiskLabel(tenant_id=tenant_id, transaction_id=txn.id, label="FRAUD", source="review_verdict"))
        s.flush()
        row = learning_store.record(s, txn, now=NOON)
        assert (row.label, row.label_source) == ("FRAUD", "review_verdict")


# ---- when a row is written -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, outcome", [("AUTHORIZED", "COMPLETED"), ("CONSUMED", "COMPLETED"), ("DENIED", "DECLINED"),
                                             ("CANCELLED", "CANCELLED"), ("EXPIRED", "EXPIRED")])
def test_every_way_a_payment_ends_writes_one_row(bank, status, outcome):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id, status=status)
        row = learning_store.record(s, txn, now=NOON)
        assert (row.outcome, row.mode, row.label) == (outcome, "COLLECTING", "UNKNOWN")
        assert learning_store.record(s, txn, now=NOON) is None              # called again: still one row
        assert len(_rows(s, tenant_id)) == 1


@pytest.mark.parametrize("status", ["PENDING_AUTH", "REVIEW"])
def test_a_payment_still_in_progress_writes_nothing(bank, status):
    tenant_id, cust_id = bank
    with db.session() as s:
        assert learning_store.record(s, _txn(s, tenant_id, cust_id, status=status), now=NOON) is None


def test_nothing_is_written_with_the_switch_off(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        s.get(db.Tenant, tenant_id).policy = _policy(enabled=False)
        s.flush()
        assert learning_store.record(s, _txn(s, tenant_id, cust_id), now=NOON) is None
        assert _rows(s, tenant_id) == []


def test_account_changes_are_not_payments_and_write_nothing(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        assert learning_store.record(s, _txn(s, tenant_id, cust_id, type="PROFILE_CHANGE"), now=NOON) is None


@pytest.mark.parametrize("view", [None, {"band": 9, "rulesLevel": "T1", "modelLevel": "T1"}, {"band": 1, "rulesLevel": "X"}])
def test_a_payment_with_no_valid_view_writes_nothing(bank, view):
    tenant_id, cust_id = bank
    with db.session() as s:
        assert learning_store.record(s, _txn(s, tenant_id, cust_id, view=view), now=NOON) is None


def test_a_failure_writing_the_row_never_touches_the_payment(bank, monkeypatch):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id, status="PENDING_AUTH")
        tid = txn.id
    with db.session() as s:
        txn = s.get(db.Transaction, tid)
        txn.status = "AUTHORIZED"
        monkeypatch.setattr(learning_store, "_band_vs_usual", lambda *a: 1 / 0)
        assert learning_store.record(s, txn, now=NOON) is None
    with db.session() as s:
        assert s.get(db.Transaction, tid).status == "AUTHORIZED"       # the payment's own change was committed
        assert _rows(s, tenant_id) == []


def test_a_failed_write_logs_the_kind_of_failure_and_none_of_the_rows_values(bank, monkeypatch, caplog):
    tenant_id, cust_id = bank
    with db.session() as s:
        txn = _txn(s, tenant_id, cust_id, amount=4_321_09)
        link = learning_store.link_of(txn.id)
        monkeypatch.setattr(learning_store, "_band_vs_usual", lambda *a: (_ for _ in ()).throw(ValueError(f"boom {link} {txn.id} 432109")))
        with caplog.at_level("ERROR", logger="amfa.learning_store"):
            assert learning_store.record(s, txn, now=NOON) is None
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "learning row not written" in logged and "ValueError" in logged
    for secret in (link, txn.id, "432109"):
        assert secret not in logged


# ---- band against the customer's usual -------------------------------------------------------------------------------------------

def test_band_against_usual(bank):
    tenant_id, cust_id = bank
    with db.session() as s:
        first = learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON - 10 * DAY, view=VIEW | {"band": 0}), now=NOON)
        assert first.band_vs_usual == "NEW"                                  # no history yet
        for i in range(3):                                                   # their usual: band 0
            _txn(s, tenant_id, cust_id, at=NOON - (9 - i) * DAY, amount=500_00, status="CONSUMED", view=None)
        _txn(s, tenant_id, cust_id, at=NOON - 5 * DAY, amount=50_000_00, status="CONSUMED", view=None)
        _txn(s, tenant_id, cust_id, at=NOON - 4 * DAY, amount=900_00, status="DENIED", view=None)      # not verified: ignored
        same = learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON, view=VIEW | {"band": 0}), now=NOON)
        two = learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON + 1, view=VIEW | {"band": 3}), now=NOON)
        one = learning_store.record(s, _txn(s, tenant_id, cust_id, at=NOON + 2, view=VIEW | {"band": 1}), now=NOON)
        assert (same.band_vs_usual, one.band_vs_usual, two.band_vs_usual) == ("SAME", "ONE_HIGHER", "TWO_HIGHER")
