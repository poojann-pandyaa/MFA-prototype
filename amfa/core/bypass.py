"""A customer's own "no extra checks up to this amount" setting (see engine/risk/threat.customer_bypass for what it does).

Two protections live here, because this setting is exactly what a fraudster who has taken over an account would want:
 * it is set only against a transaction of type CHANGE_LIMIT that passed its checks and whose proof was consumed, used once, so changing it needs
   the strong checks that were asked, never just a signed-in session;
 * a raise waits out the tenant's cooling-off before it counts; lowering or removing is immediate.
"""
from __future__ import annotations

import time
from typing import Optional

from amfa.common.web import ApiError
from amfa.core import db
from amfa.engine.policy import TenantPolicy, platform_bypass_caps


def current(row: Optional[db.BypassLimit], now: float) -> int:
    """The limit in force at [now] (a raise that has finished its cooling-off counts)."""
    if row is None:
        return 0
    if row.pending_minor is not None and row.pending_from is not None and now >= row.pending_from:
        return row.pending_minor
    return row.limit_minor


def limit_for(s, customer_id: str, policy: TenantPolicy, currency: str, now: float) -> Optional[int]:
    """What the engine uses: the customer's limit, never above what the bank and the platform allow; None when off."""
    cb = policy.customerBypass
    if not cb.enabled or currency != policy.currency:
        return None
    ceiling = min(cb.maxMinor, platform_bypass_caps().get(currency, 0))
    value = min(current(s.get(db.BypassLimit, customer_id), now), ceiling)
    return value or None


def view(s, customer_id: str, policy: TenantPolicy, now: float) -> dict:
    row = s.get(db.BypassLimit, customer_id)
    cb = policy.customerBypass
    pending = row is not None and row.pending_minor is not None and row.pending_from is not None and now < row.pending_from
    return {"enabled": cb.enabled, "currency": policy.currency, "maxMinor": cb.maxMinor,
            "limitMinor": limit_for(s, customer_id, policy, policy.currency, now) or 0,
            "pendingMinor": row.pending_minor if pending else None,
            "pendingFrom": int(row.pending_from) if pending else None}


def set_limit(s, customer: db.Customer, policy: TenantPolicy, amount_minor: int, txn: db.Transaction, now: float) -> dict:
    """Set the limit against a CHANGE_LIMIT transaction for the same amount whose checks passed and whose proof the bank has
    just consumed (status CONSUMED: that is the bank saying "the customer really approved this"). Marks it used."""
    cb = policy.customerBypass
    if not cb.enabled:
        raise ApiError(409, "BYPASS_DISABLED", "this bank does not let customers set a no-extra-check limit")
    if amount_minor > min(cb.maxMinor, platform_bypass_caps().get(policy.currency, 0)):
        raise ApiError(422, "BYPASS_ABOVE_MAX", "that is above the most this bank allows")
    sess = s.get(db.AuthSession, txn.session_id) if txn.session_id else None
    if (txn.customer_id != customer.id or txn.type != "CHANGE_LIMIT" or txn.status != "CONSUMED"
            or txn.amount_minor != amount_minor or (txn.facts or {}).get("bypassUsed")
            or sess is None or now - (sess.resolved_at or 0) > policy.proofTtlSeconds * 5):
        raise ApiError(409, "BYPASS_NOT_AUTHORISED", "the limit can be set only with a fresh, verified change-limit "
                       "transaction for this customer and this amount, used once")
    txn.facts = {**(txn.facts or {}), "bypassUsed": True}
    row = s.get(db.BypassLimit, customer.id)
    if row is None:
        row = db.BypassLimit(customer_id=customer.id, limit_minor=0)
        s.add(row)
    now_in_force = current(row, now)
    row.limit_minor, row.pending_minor, row.pending_from = now_in_force, None, None
    if amount_minor <= now_in_force or cb.coolingOffHours == 0:
        row.limit_minor = amount_minor
    else:
        row.pending_minor, row.pending_from = amount_minor, now + cb.coolingOffHours * 3600
    row.updated_at = now
    s.flush()
    return view(s, customer.id, policy, now)


def clear(s, customer: db.Customer, policy: TenantPolicy, now: float) -> dict:
    """Removing it only ever lowers risk, so it needs no extra proof and takes effect at once."""
    row = s.get(db.BypassLimit, customer.id)
    if row is not None:
        s.delete(row)
        s.flush()
    return view(s, customer.id, policy, now)
