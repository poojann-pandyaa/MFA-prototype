"""What a customer tells us at sign-up, and how customers like them really pay.

A new customer has no history, so the model has nothing of theirs to compare a payment with. Two optional answers (a typical payment, and
roughly a month's total, each a band) give it a starting point (engine/risk/declared.py). Once enough customers who answered the same way
have become READY, their actual payments are a better starting point than the answer, and replace it (`refresh`, run nightly).

Bands, never exact figures, and only about the customer who gave them; a peer group is shown to the model only as a centre and a
spread over at least MIN_GROUP customers, so nobody in it can be picked out.
"""
from __future__ import annotations

import math
import time
from statistics import median
from typing import Optional

from sqlalchemy import select

from amfa.core import db
from amfa.engine.risk import declared
from amfa.engine.risk.readiness import DAY

MIN_GROUP = 50
MAX_REGULAR_PAYEES = 3
MONEY = ("TRANSFER", "PAYMENT", "CARD_NOT_PRESENT")


def group_key(typical_band: Optional[int]) -> Optional[str]:
    return None if typical_band is None else f"typical:{typical_band}"


def save(s, customer: db.Customer, *, month_band: Optional[int], typical_band: Optional[int],
         regular_payee_keys: list[str]) -> db.CustomerProfile:
    """[regular_payee_keys] are hashed payee keys the bank vouches for (it checked the account name); at most MAX_REGULAR_PAYEES."""
    row = s.get(db.CustomerProfile, customer.id)
    keys = list(dict.fromkeys(regular_payee_keys))[:MAX_REGULAR_PAYEES]
    if row is None:
        row = db.CustomerProfile(customer_id=customer.id)
        s.add(row)
    row.month_band, row.typical_band, row.regular_payees, row.declared_at = month_band, typical_band, keys, time.time()
    s.flush()
    return row


def clear(s, customer_id: str) -> None:
    row = s.get(db.CustomerProfile, customer_id)
    if row is not None:
        s.delete(row)
        s.flush()


def view(row: Optional[db.CustomerProfile]) -> dict:
    return {"declared": row is not None and (row.month_band is not None or row.typical_band is not None),
            "monthBand": None if row is None else row.month_band, "typicalBand": None if row is None else row.typical_band,
            "regularPayees": 0 if row is None else len(row.regular_payees or []),
            "monthLabels": declared.MONTH_LABELS, "typicalLabels": declared.TYPICAL_LABELS}


def prior(s, tenant_id: str, row: Optional[db.CustomerProfile]) -> Optional[tuple[float, float]]:
    """(centre, spread) of the habit score before they have history: how their peers really pay if enough are known, otherwise the
    typical payment they named. None when they skipped the question."""
    if row is None or row.typical_band is None:
        return None
    stat = s.execute(select(db.PeerGroupStat).where(db.PeerGroupStat.tenant_id == tenant_id,
                                                     db.PeerGroupStat.group_key == group_key(row.typical_band))).scalars().first()
    if stat is not None and stat.n_customers >= MIN_GROUP:
        return stat.centre_log, stat.spread_log
    return declared.peer_prior(declared.typical_of(row.typical_band))


def refresh(s, now: Optional[float] = None) -> int:
    """From the READY customers in each group: the centre and spread of their log payment amounts over the last 90 days. A group with
    fewer than MIN_GROUP ready customers keeps no row (and a stale one is removed), so the declared figure stands in."""
    from amfa.core.phase import READY
    now = now or time.time()
    since = now - 90 * DAY
    written = 0
    tenants = {t for (t,) in s.execute(select(db.Customer.tenant_id).distinct())}
    for tenant_id in tenants:
        groups: dict[str, set[str]] = {}
        for cid, band in s.execute(select(db.CustomerProfile.customer_id, db.CustomerProfile.typical_band).join(
                db.CustomerPhase, db.CustomerPhase.customer_id == db.CustomerProfile.customer_id).join(
                db.Customer, db.Customer.id == db.CustomerProfile.customer_id).where(
                db.Customer.tenant_id == tenant_id, db.CustomerPhase.phase == READY, db.CustomerProfile.typical_band.is_not(None))):
            groups.setdefault(group_key(band), set()).add(cid)
        for key in {r.group_key for r in s.execute(select(db.PeerGroupStat).where(db.PeerGroupStat.tenant_id == tenant_id)).scalars()} | set(groups):
            ids = groups.get(key, set())
            existing = s.execute(select(db.PeerGroupStat).where(db.PeerGroupStat.tenant_id == tenant_id, db.PeerGroupStat.group_key == key)).scalars().first()
            logs = [math.log1p(a / 100.0) for (a,) in s.execute(select(db.Transaction.amount_minor).where(
                db.Transaction.customer_id.in_(ids), db.Transaction.type.in_(MONEY), db.Transaction.created_at >= since,
                db.Transaction.status.in_(("AUTHORIZED", "CONSUMED"))))] if len(ids) >= MIN_GROUP else []
            if len(ids) < MIN_GROUP or len(logs) < MIN_GROUP:
                if existing is not None:
                    s.delete(existing)
                continue
            m = median(logs)
            spread = max(1.4826 * median(abs(v - m) for v in logs), 0.3)
            if existing is None:
                existing = db.PeerGroupStat(tenant_id=tenant_id, group_key=key, n_customers=0, centre_log=m, spread_log=spread)
                s.add(existing)
            existing.n_customers, existing.centre_log, existing.spread_log, existing.computed_at = len(ids), m, spread, now
            written += 1
    s.flush()
    return written
