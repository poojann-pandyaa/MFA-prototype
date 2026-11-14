"""Learning or ready: does the model know this customer well enough to choose their checks?

Readiness is a test on the customer's own verified payments (engine/risk/readiness.py): the model has been right about them, their
usual amount is pinned down, and the evidence spans separate days. Until it passes the customer is LEARNING: payments are confirmed
with the phone's own fingerprint or screen lock, backed by limits and the hard warning signs (engine/decide.learning_plan). The
model still scores every payment; whether it would have asked for a face check is the mark the test learns from.
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import select

from amfa.core import db
from amfa.engine.policy import TenantPolicy
from amfa.engine.risk.readiness import Tracker

LEARNING, READY = "LEARNING", "READY"
MONEY = ("TRANSFER", "PAYMENT", "CARD_NOT_PRESENT")


def _row(s, customer_id: str) -> Optional[db.CustomerPhase]:
    return s.get(db.CustomerPhase, customer_id)


def ensure(s, customer: db.Customer, now: Optional[float] = None, exclude: Optional[str] = None) -> db.CustomerPhase:
    """The customer's phase row. A customer with no row yet (one who existed before this was switched on) is read from their stored
    history: their past verified payments are replayed, with no false-alarm marks since none were recorded."""
    row = _row(s, customer.id)
    if row is not None:
        return row
    now = now or time.time()
    tr = Tracker()
    txns = s.execute(select(db.Transaction).where(
        db.Transaction.customer_id == customer.id, db.Transaction.type.in_(MONEY),
        db.Transaction.status.in_(("AUTHORIZED", "CONSUMED")), db.Transaction.id != (exclude or "")).order_by(db.Transaction.created_at)).scalars().all()
    for t in txns:
        tr.observe(t.amount_minor / 100.0, False, t.created_at)
    row = db.CustomerPhase(customer_id=customer.id, phase=READY if tr.ready() else LEARNING,
                           ready_since=now if tr.ready() else None, evidence=tr.to_dict(), verified_payments=len(txns), updated_at=now)
    s.add(row)
    s.flush()
    return row


def current(s, customer: db.Customer, policy: TenantPolicy, now: Optional[float] = None) -> str:
    """READY for everyone when the bank has not switched the learning phase on."""
    if not policy.learning.enabled:
        return READY
    return ensure(s, customer, now).phase


def observe(s, customer: db.Customer, txn: db.Transaction, would_have_alarmed: bool, now: Optional[float] = None) -> db.CustomerPhase:
    """A payment was verified: it teaches the model this customer. [would_have_alarmed] is whether the model, with the customer's own
    habit, would have asked for a face check or more."""
    now = now or time.time()
    row = ensure(s, customer, now, exclude=txn.id)      # the payment being learned from is added below, not replayed
    if txn.type not in MONEY:
        return row
    tr = Tracker.from_dict(row.evidence or {})
    tr.observe(txn.amount_minor / 100.0, would_have_alarmed, txn.created_at or now)
    row.evidence = tr.to_dict()
    row.verified_payments = (row.verified_payments or 0) + 1
    row.updated_at = now
    if tr.ready():
        if row.phase != READY:
            row.phase, row.ready_since = READY, now
    elif row.phase == READY:           # their recent payments started drawing false alarms again: learn some more
        row.phase, row.ready_since = LEARNING, None
    return row


def reset(s, customer_id: str, now: Optional[float] = None) -> None:
    """A fraud report on this customer: what the model thought it knew about them is not to be trusted. Start learning again."""
    row = _row(s, customer_id)
    if row is not None:
        row.phase, row.ready_since, row.evidence, row.verified_payments = LEARNING, None, {}, 0
        row.updated_at = now or time.time()


def view(s, customer: db.Customer, policy: TenantPolicy, now: Optional[float] = None) -> dict:
    if not policy.learning.enabled:
        return {"enabled": False, "phase": READY}
    row = ensure(s, customer, now)
    tr = Tracker.from_dict(row.evidence or {})
    out = {"enabled": True, "phase": row.phase, "readySince": int(row.ready_since) if row.ready_since else None,
           "verifiedPayments": row.verified_payments or 0,
           "offerBiometrics": (row.verified_payments or 0) >= policy.learning.offerBiometricsAfter}
    if row.phase == LEARNING:
        out["progress"] = tr.progress()
    return out
