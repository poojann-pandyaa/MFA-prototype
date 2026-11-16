"""The band switch's anonymous learning store (docs/rbi-banded-data-collection.md, phase 2).

When a payment settles (completed, declined, cancelled or expired) and the bank has the band switch on, one `learning_row` is
written: the payment's amount band and a few small categories, what the rules and the model would each have asked for, and how it
ended. It never holds a customer, account, amount, payee, device, network, place, address or exact time.

A label (the bank's fraud or genuine report, a reviewer's verdict) can arrive weeks later. It finds its row through `link`, a keyed
hash of the transaction id made with AMFA's index key, so neither a leaked table nor anyone without the key can compute it. The link
is wiped after the retention window (the same 90 days the transaction itself is kept); from then on the row belongs to no one.

Flooding guard: only a customer's first `collection.dailyRowsPerCustomer` payments of a local day become rows. That is counted
from the existing transaction table at write time, so the row itself needs no customer id.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select, update

from amfa import config
from amfa.common import crypto
from amfa.core import db
from amfa.engine.band_switch import LEVELS, MONEY
from amfa.engine.policy import TenantPolicy
from amfa.engine.risk.collection_bands import band_of

log = logging.getLogger("amfa.learning_store")
DAY = 86_400.0

OUTCOMES = {"AUTHORIZED": "COMPLETED", "CONSUMED": "COMPLETED", "DENIED": "DECLINED", "CANCELLED": "CANCELLED",
            "EXPIRED": "EXPIRED"}
VERIFIED = ("AUTHORIZED", "CONSUMED")
MONEY_TYPES = tuple(t.value for t in MONEY)
# who may label a row: the bank (through its scoped API key) and the four-eyes review. Anything else is ignored.
LABEL_SOURCES = {"customer_report", "chargeback", "bank_ops", "review_verdict"}
LABELS = {"FRAUD", "GENUINE"}
MODES = {"COLLECTING", "ELIGIBLE", "MODEL"}       # the band's state when the payment was decided


def link_of(txn_id: str) -> str:
    return crypto.keyed_hash(txn_id, "learning-link", length=64)


def _policy(s, tenant_id: str) -> Optional[TenantPolicy]:
    tenant = s.get(db.Tenant, tenant_id)
    return TenantPolicy.model_validate(tenant.policy) if tenant is not None else None


def _local(at: float, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(at, tz)


def hour_bucket(hour: int) -> str:
    return ("NIGHT", "MORNING", "AFTERNOON", "EVENING")[hour // 6]


def week_start(at: float, tz: ZoneInfo) -> str:
    d = _local(at, tz).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _within_daily_cap(s, txn: db.Transaction, tz: ZoneInfo, cap: int) -> bool:
    """Is this among the customer's first [cap] payments of its local day? Ordered by creation, then id, so it is stable."""
    d = _local(txn.created_at, tz).date()
    start = datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()
    earlier = s.execute(select(func.count()).select_from(db.Transaction).where(
        db.Transaction.customer_id == txn.customer_id, db.Transaction.type.in_(MONEY_TYPES),
        db.Transaction.created_at >= start,
        or_(db.Transaction.created_at < txn.created_at,
            and_(db.Transaction.created_at == txn.created_at, db.Transaction.id < txn.id)))).scalar_one()
    return earlier < cap


def _band_vs_usual(s, txn: db.Transaction, band: int) -> str:
    """This payment's band against the band the customer most often pays in, over their verified payments of the last 90 days.
    Worked out here and only the result kept. A tie goes to the lower band (the payment then looks no less unusual)."""
    since = txn.created_at - config.get().retention_days * DAY
    amounts = s.execute(select(db.Transaction.amount_minor).where(
        db.Transaction.customer_id == txn.customer_id, db.Transaction.type.in_(MONEY_TYPES),
        db.Transaction.status.in_(VERIFIED), db.Transaction.id != txn.id,
        db.Transaction.created_at >= since, db.Transaction.created_at < txn.created_at)).scalars().all()
    if not amounts:
        return "NEW"
    counts = Counter(band_of(a) for a in amounts)
    usual = min(counts, key=lambda b: (-counts[b], b))
    diff = band - usual
    return "LOWER" if diff < 0 else ("SAME", "ONE_HIGHER")[diff] if diff < 2 else "TWO_HIGHER"


def _view(s, txn: db.Transaction) -> Optional[dict]:
    d = s.execute(select(db.Decision).where(db.Decision.transaction_id == txn.id)
                  .order_by(db.Decision.id.desc())).scalars().first()
    v = ((d.plan if d else None) or {}).get("bandView")
    ok = (isinstance(v, dict) and v.get("band") in (0, 1, 2, 3) and v.get("rulesLevel") in LEVELS
          and v.get("modelLevel") in LEVELS)
    return v if ok else None


def record(s, txn: db.Transaction, now: Optional[float] = None, mode: str = "COLLECTING") -> Optional[db.LearningRow]:
    """Write the learning row for a payment that has just settled, if the bank has the band switch on. Writes at most one row per
    payment, whatever calls it twice. Never raises, and runs in a savepoint: a failure here rolls back only the row, never the
    payment."""
    try:
        with s.begin_nested():
            return _record(s, txn, now or time.time(), mode)
    except Exception as e:
        # the kind of failure only: a database error's text can carry the row's values, the label link among them
        log.error("learning row not written", extra={"error": type(e).__name__})
        return None


def _record(s, txn: db.Transaction, now: float, mode: str) -> Optional[db.LearningRow]:
    outcome = OUTCOMES.get(txn.status)
    if outcome is None or txn.type not in MONEY_TYPES:
        return None
    policy = _policy(s, txn.tenant_id)
    if policy is None or not policy.collection.enabled:
        return None
    link = link_of(txn.id)
    if s.execute(select(db.LearningRow.id).where(db.LearningRow.link == link)).first() is not None:
        return None
    view = _view(s, txn)
    if view is None:                                   # decided before the switch was on, or not a payment
        return None
    tz = ZoneInfo(policy.timezone)
    if not _within_daily_cap(s, txn, tz, policy.collection.dailyRowsPerCustomer):
        return None
    expires = math.ceil((now + config.get().retention_days * DAY) / DAY) * DAY      # a whole day, never the settle time
    row = db.LearningRow(
        id=uuid.uuid4().hex, tenant_id=txn.tenant_id, band=view["band"], band_vs_usual=_band_vs_usual(s, txn, view["band"]),
        payee_kind=view["payeeKind"], device_kind=view["deviceKind"], hard_sign=bool(view["hardSign"]),
        hour_bucket=hour_bucket(_local(txn.created_at, tz).hour), rules_level=view["rulesLevel"], model_level=view["modelLevel"],
        mode=view.get("mode") if view.get("mode") in MODES else mode, outcome=outcome, label="UNKNOWN", label_source=None, week_start=week_start(txn.created_at, tz),
        link=link, link_expires_at=expires)
    s.add(row)
    s.flush()
    for lab in s.execute(select(db.RiskLabel).where(db.RiskLabel.transaction_id == txn.id)
                         .order_by(db.RiskLabel.reported_at, db.RiskLabel.id)).scalars():
        _apply(row, lab.label, lab.source)            # a label that arrived before the payment settled
    _after_label(s, row)
    return row


def _after_label(s, row: db.LearningRow) -> None:
    if row.label == "FRAUD":
        from amfa.core import band_gate
        band_gate.on_fraud_label(s, row)


def _apply(row: db.LearningRow, label: str, source: str) -> bool:
    """FRAUD sticks: a later GENUINE never clears it, so no report can make a fraud case vanish from the grading."""
    if label not in LABELS or source not in LABEL_SOURCES:
        return False
    if row.label == "FRAUD" and label != "FRAUD":
        return False
    row.label, row.label_source = label, source
    return True


def apply_label(s, txn_id: str, label: str, source: str, now: Optional[float] = None) -> bool:
    """A label for a payment: update its row if the row exists and its link has not expired. A fraud label in the top band may send
    that band back to the rules (band_gate.on_fraud_label). Never raises, and runs in a savepoint so the label's own record stays."""
    if label not in LABELS or source not in LABEL_SOURCES:
        return False
    now = now or time.time()
    try:
        with s.begin_nested():
            row = s.execute(select(db.LearningRow).where(db.LearningRow.link == link_of(txn_id))).scalars().first()
            if row is None or row.link_expires_at is None or row.link_expires_at <= now:
                return False
            if not _apply(row, label, source):
                return False
            _after_label(s, row)
            return True
    except Exception as e:
        log.error("learning row label not applied", extra={"error": type(e).__name__})
        return False


def purge(s, now: Optional[float] = None) -> dict[str, int]:
    """Wipe expired links (whether or not the switch is still on), then delete rows older than each bank's retention."""
    now = now or time.time()
    out = {"learning_link": s.execute(update(db.LearningRow).where(db.LearningRow.link_expires_at <= now)
                                      .values(link=None, link_expires_at=None)).rowcount or 0}
    gone = 0
    for tenant_id in s.execute(select(db.LearningRow.tenant_id).distinct()).scalars().all():
        policy = _policy(s, tenant_id)
        keep = policy.collection.retentionDays if policy else 0          # a bank that is gone keeps nothing
        tz = ZoneInfo(policy.timezone) if policy else ZoneInfo("UTC")
        cutoff = (_local(now, tz).date() - timedelta(days=keep)).isoformat()
        gone += s.execute(delete(db.LearningRow).where(db.LearningRow.tenant_id == tenant_id,
                                                       db.LearningRow.week_start < cutoff)).rowcount or 0
    out["learning_row"] = gone
    return out
