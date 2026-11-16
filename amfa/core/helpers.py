"""Optional family helpers: a customer who gets help paying (an older parent, a relative abroad) may register that family member's
phone. A payment from a registered helper phone is then expected instead of strange, which is what removes the extra checks genuine
help used to cause (the model reads it as `helper_device`; see engine/risk/features.py).

It is what a fraudster who has taken over an account would also like to have, so:
 * an invite is made only against a CHANGE_LIMIT transaction (payee "HELPER", amount 0) that passed its checks and whose proof
   the bank consumed, used once, so it needs the strong checks, never just a signed-in session;
 * the helper's phone redeems a short-lived, single-use code shown on the customer's own phone;
 * a new helper counts only after the bank's cooling-off; removing one is immediate and needs nothing;
 * a bank turns the feature on (`familyHelpers.enabled`) and a customer never has to use it.
"""
from __future__ import annotations

import secrets
import time
import uuid
from typing import Optional

from sqlalchemy import select

from amfa.common import crypto
from amfa.common.web import ApiError
from amfa.core import db
from amfa.engine.policy import TenantPolicy

PAYEE_REF = "HELPER"


def _code_hash(tenant_id: str, code: str) -> str:
    return crypto.keyed_hash(f"{tenant_id}|{code}", "helper-invite")


def _rows(s, customer_id: str) -> list[db.TrustedHelper]:
    return list(s.execute(select(db.TrustedHelper).where(db.TrustedHelper.customer_id == customer_id)
                          .order_by(db.TrustedHelper.created_at)).scalars())


def active_keys(s, customer_id: str, now: float) -> frozenset:
    """Device keys of helper phones in force at [now]."""
    return frozenset(h.device_key for h in _rows(s, customer_id)
                     if h.device_key and h.active_from is not None and now >= h.active_from)


def view(s, customer_id: str, policy: TenantPolicy, now: float) -> dict:
    fh = policy.familyHelpers
    out = []
    for h in _rows(s, customer_id):
        if h.device_key:
            state = "ACTIVE" if h.active_from is not None and now >= h.active_from else "WAITING"
        elif h.invite_expires > now:
            state = "INVITED"
        else:
            continue   # an unused, expired invite is nothing
        out.append({"id": h.id, "label": h.label, "state": state,
                    "activeFrom": int(h.active_from) if state == "WAITING" and h.active_from else None,
                    "inviteExpires": int(h.invite_expires) if state == "INVITED" else None})
    return {"enabled": fh.enabled, "max": fh.maxHelpers, "coolingOffHours": fh.coolingOffHours, "helpers": out}


def invite(s, customer: db.Customer, policy: TenantPolicy, label: str, txn: db.Transaction, now: float) -> dict:
    """Create a single-use invite code. The code is returned once and only its hash is kept."""
    fh = policy.familyHelpers
    if not fh.enabled:
        raise ApiError(409, "HELPERS_DISABLED", "this bank does not offer family helpers")
    sess = s.get(db.AuthSession, txn.session_id) if txn.session_id else None
    if (txn.customer_id != customer.id or txn.type != "CHANGE_LIMIT" or txn.status != "CONSUMED"
            or txn.payee_ref != PAYEE_REF or txn.amount_minor != 0 or (txn.facts or {}).get("helperUsed")
            or sess is None or now - (sess.resolved_at or 0) > policy.proofTtlSeconds * 5):
        raise ApiError(409, "HELPER_NOT_AUTHORISED", "a helper can be added only with a fresh, verified helper approval "
                       "for this customer, used once")
    live = [h for h in view(s, customer.id, policy, now)["helpers"]]
    if len(live) >= fh.maxHelpers:
        raise ApiError(409, "HELPERS_FULL", f"at most {fh.maxHelpers} helpers; remove one first")
    txn.facts = {**(txn.facts or {}), "helperUsed": True}
    code = "".join(secrets.choice("0123456789") for _ in range(8))
    s.add(db.TrustedHelper(id=f"hlp_{uuid.uuid4().hex[:16]}", customer_id=customer.id, label=label.strip()[:24] or "Family",
                           invite_hash=_code_hash(customer.tenant_id, code), invite_expires=now + fh.inviteMinutes * 60))
    s.flush()
    return {"code": code, "expiresAt": int(now + fh.inviteMinutes * 60), **view(s, customer.id, policy, now)}


def redeem(s, customer: db.Customer, policy: TenantPolicy, code: str, device_key: str, now: float) -> dict:
    """The helper's phone presents the code. It becomes a helper after the cooling-off."""
    fh = policy.familyHelpers
    if not fh.enabled:
        raise ApiError(409, "HELPERS_DISABLED", "this bank does not offer family helpers")
    row = s.execute(select(db.TrustedHelper).where(
        db.TrustedHelper.customer_id == customer.id,
        db.TrustedHelper.invite_hash == _code_hash(customer.tenant_id, code),
        db.TrustedHelper.device_key.is_(None))).scalars().first()
    if row is None or row.invite_expires < now:
        raise ApiError(404, "INVITE_INVALID", "that code is wrong or has expired")
    if device_key in active_keys(s, customer.id, now + 10 ** 9):
        raise ApiError(409, "ALREADY_HELPER", "this phone is already a helper")
    row.device_key, row.invite_hash, row.invite_expires = device_key, None, 0.0
    row.active_from = now + fh.coolingOffHours * 3600
    s.flush()
    return view(s, customer.id, policy, now)


def remove(s, customer_id: str, helper_id: str, policy: TenantPolicy, now: float) -> dict:
    """Removing a helper only ever lowers trust, so it needs no proof and is immediate."""
    row = s.get(db.TrustedHelper, helper_id)
    if row is None or row.customer_id != customer_id:
        raise ApiError(404, "HELPER_NOT_FOUND", "no such helper")
    s.delete(row)
    s.flush()
    return view(s, customer_id, policy, now)
