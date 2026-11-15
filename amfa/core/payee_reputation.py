"""Who is receiving the money? A payee that suddenly takes money from many customers, or that customers have reported, is the classic
mule account. This protects a new customer exactly when the model has no history of theirs, because it needs none: it looks across
the bank's other customers.

It uses only what AMFA already keeps. `known_payee` has one row per customer and payee (hashed key, first time they paid it), so the
rows for one payee are its senders and when each one arrived; `risk_label` holds the fraud reports and `txn` says which payee each was
for. A payee older than `establishedDays` with no reports (a shop, a utility) is never flagged, however many customers pay it.

The answer feeds the risk model's existing `payee_risk_flag`, which is a hard warning sign: it keeps the model's level whatever
the payment's size or the customer's habit, and holds a large payment for a person. Rules turned into one flag, so no retraining.
Opt in with `payeeReputation.enabled` in the tenant policy.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select

from amfa.core import db
from amfa.engine.policy import TenantPolicy

DAY = 86_400.0
REPORT_WINDOW_DAYS = 90


def evaluate(s, tenant_id: str, policy: TenantPolicy, customer_id: str, payee_ref: str, payee_key: str, now: float,
             registry_created_at: Optional[float] = None) -> dict:
    """What the bank's other customers tell us about this payee. [registry_created_at] is when the bank's own registry says the
    payee was added (older evidence of an established payee than anything AMFA saw)."""
    cfg = policy.payeeReputation
    rows = s.execute(select(db.KnownPayee.customer_id, db.KnownPayee.first_seen).where(
        db.KnownPayee.payee_key == payee_key, db.KnownPayee.customer_id != customer_id)).all()
    firsts = [r.first_seen for r in rows] + ([registry_created_at] if registry_created_at else [])
    age_days = (now - min(firsts)) / DAY if firsts else None
    s7 = [r for r in rows if now - r.first_seen <= 7 * DAY]
    s1 = [r for r in rows if now - r.first_seen <= DAY]
    # how many of the recent senders are themselves new customers (still being learned): the mule's usual victims
    recent_ids = [r.customer_id for r in s7]
    new_senders = 0
    if recent_ids:
        new_senders = s.execute(select(func.count()).select_from(db.Customer).where(
            db.Customer.id.in_(recent_ids), db.Customer.created_at >= now - 30 * DAY)).scalar_one()
    reports = s.execute(select(func.count(func.distinct(db.Transaction.customer_id))).select_from(db.RiskLabel).join(
        db.Transaction, db.Transaction.id == db.RiskLabel.transaction_id).where(
        db.RiskLabel.tenant_id == tenant_id, db.RiskLabel.label == "FRAUD", db.Transaction.payee_ref == payee_ref,
        db.Transaction.customer_id != customer_id, db.RiskLabel.reported_at >= now - REPORT_WINDOW_DAYS * DAY)).scalar_one()
    reasons: list[str] = []
    established = age_days is not None and age_days > cfg.establishedDays
    if reports >= cfg.reportsFlag:
        reasons.append(f"reported as fraud by {reports} other customer{'s' if reports != 1 else ''}")
    if not established:
        if (age_days is None or age_days <= cfg.newPayeeDays) and len(s7) >= cfg.minSenders:
            reasons.append(f"a new payee that {len(s7)} different customers have paid this week")
        elif len(s1) >= cfg.burstSenders:
            reasons.append(f"{len(s1)} different customers paid it in the last day")
    return {"flag": bool(reasons), "reasons": reasons, "senders7d": len(s7), "senders24h": len(s1), "newCustomerSenders": new_senders,
            "reports": reports, "ageDays": None if age_days is None else round(age_days, 1)}
