"""Deletes what is older than the retention window (90 days by default; AMFA_RETENTION_DAYS).

The bank may keep a customer's transactions for 90 days at most, and the risk model reads no more than that, so everything
derived from a transaction goes with it: the decision record and its inputs, the approval session with its codes and attempts,
the proof, review case and fraud label, webhook deliveries, security events, the places a network was seen at, and the payee
relationships that no transaction in the window explains any more. Customers, enrolments (templates), devices, tenants, keys
and the hash-chained audit log are not transactions and stay; the audit log holds ids and outcomes, never amounts or payees.
The band switch's anonymous learning rows (core/learning_store.py) lose their label link at the same 90 days and are themselves
deleted after the bank's own `collection.retentionDays`.

Run by the core's sweeper about hourly, or by hand: python -m amfa.cli purge
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy import delete, select

from amfa import config
from amfa.core import db, learning_store
from amfa.core.context_builder import payee_key

log = logging.getLogger("amfa.retention")
DAY = 86_400.0


def purge(s, now: Optional[float] = None, days: Optional[int] = None) -> dict[str, int]:
    """Delete everything older than [days] before [now]. Returns how many rows went from each table."""
    now = time.time() if now is None else now
    cutoff = now - (days or config.get().retention_days) * DAY
    out: dict[str, int] = {}

    def run(name, stmt):
        out[name] = s.execute(stmt).rowcount or 0

    txn_ids = select(db.Transaction.id).where(db.Transaction.created_at < cutoff)
    sess_ids = select(db.AuthSession.id).where(db.AuthSession.transaction_id.in_(txn_ids))
    for model in (db.Challenge, db.OtpCode, db.Attempt):
        run(model.__tablename__, delete(model).where(model.session_id.in_(sess_ids)))
    run("auth_session", delete(db.AuthSession).where(db.AuthSession.transaction_id.in_(txn_ids)))
    run("proof", delete(db.Proof).where(db.Proof.transaction_id.in_(txn_ids)))
    run("review_case", delete(db.ReviewCase).where(db.ReviewCase.transaction_id.in_(txn_ids)))
    run("risk_label", delete(db.RiskLabel).where(db.RiskLabel.transaction_id.in_(txn_ids)))
    run("decision", delete(db.Decision).where(db.Decision.decided_at < cutoff))
    run("webhook_delivery", delete(db.WebhookDelivery).where(db.WebhookDelivery.created_at < cutoff))
    run("security_event", delete(db.SecurityEvent).where(db.SecurityEvent.at < cutoff))
    run("known_network", delete(db.KnownNetwork).where(db.KnownNetwork.last_seen < cutoff))

    # a payee relationship older than the window survives only if a transaction inside the window still explains it
    # ponytail: one pass over the window's transactions per run; add a last_seen column to known_payee if that gets large
    live = {(r.customer_id, payee_key(r.tenant_id, r.payee_ref)) for r in s.execute(
        select(db.Transaction.customer_id, db.Transaction.tenant_id, db.Transaction.payee_ref)
        .where(db.Transaction.created_at >= cutoff)).all()}
    stale = [p for p in s.execute(select(db.KnownPayee).where(db.KnownPayee.first_seen < cutoff)).scalars()
             if (p.customer_id, p.payee_key) not in live]
    for p in stale:
        s.delete(p)
    out["known_payee"] = len(stale)
    run("txn", delete(db.Transaction).where(db.Transaction.created_at < cutoff))
    # the band switch's anonymous rows: their label links expire with the transactions, the rows by each bank's own retention
    out.update(learning_store.purge(s, now))
    if any(out.values()):
        log.info("retention purge", extra={"deleted": out})
    return out
