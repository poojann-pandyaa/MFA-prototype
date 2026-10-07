from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from amfa.common import geo
from amfa.common.crypto import keyed_hash
from amfa.core import db, factors
from amfa.engine.context import (Band, BehaviouralCtx, Capability, Context, DeviceCtx, Enrollment,
                                 Policy, Segment, TransactionCtx, TxnType)
from amfa.engine.policy import TenantPolicy

HOUR_HISTORY_MIN = 8
HOUR_DEVIATION_UNUSUAL = 3.0


@dataclass
class TxnInput:
    type: TxnType
    amount_minor: int
    currency: str
    payee_ref: str
    channel: str
    device_id: str
    ip: str
    payee_created_at: Optional[float] = None
    device_first_seen: Optional[float] = None
    capability: Optional[dict] = None
    session: Optional[dict] = None
    at: float = field(default_factory=time.time)


@dataclass
class Assembled:
    ctx: Context
    preview: dict
    facts: dict
    derived: dict
    policy: TenantPolicy


def device_key(tenant_id: str, device_id: str) -> str:
    return keyed_hash(f"{tenant_id}|{device_id}", "device")


def payee_key(tenant_id: str, payee_ref: str) -> str:
    return keyed_hash(f"{tenant_id}|{payee_ref}", "payee")


def _hour_deviation(hour: int, histogram: list[int]) -> float:
    total = sum(histogram)
    if total == 0:
        return 0.0
    mx = sum(n * math.cos(2 * math.pi * h / 24) for h, n in enumerate(histogram)) / total
    my = sum(n * math.sin(2 * math.pi * h / 24) for h, n in enumerate(histogram)) / total
    conc = math.hypot(mx, my)
    if conc < 1e-9:
        return 0.0
    mean = math.atan2(my, mx)
    this = 2 * math.pi * hour / 24
    diff = abs(math.atan2(math.sin(this - mean), math.cos(this - mean)))
    return diff * 24 / (2 * math.pi) * conc


def enrollment_of(s, customer: db.Customer) -> Enrollment:
    rows = {e.factor: e for e in s.execute(select(db.Enrollment).where(
        db.Enrollment.customer_id == customer.id, db.Enrollment.status == "ENROLLED")).scalars()}
    ueba = rows.get("UEBA")
    return Enrollment(
        otp="OTP" in rows or bool(customer.contact_sealed),
        face="FACE" in rows,
        voice="VOICE" in rows,
        keystroke=ueba is not None,
        shape=bool(ueba and (ueba.detail or {}).get("shape")),
    )


def assemble(s, tenant: db.Tenant, customer: db.Customer, txn_id: str, t: TxnInput) -> Assembled:
    policy = TenantPolicy.model_validate(tenant.policy)
    derived: dict = {}
    now = t.at

    band = policy.band_for(t.amount_minor, t.currency)
    dkey = device_key(tenant.id, t.device_id)
    pkey = payee_key(tenant.id, t.payee_ref)
    place = geo.locate(t.ip)

    dev = s.execute(select(db.Device).where(db.Device.customer_id == customer.id,
                                            db.Device.device_key == dkey)).scalars().first()
    trust_s = policy.deviceTrustDays * 86_400
    ours = bool(dev and dev.verified_count >= 1 and now - dev.first_seen >= trust_s)
    bank = bool(t.device_first_seen and now - t.device_first_seen >= max(trust_s, 86_400))
    device_known = ours or bank
    derived["device"] = {"registered": dev is not None, "verifiedUses": dev.verified_count if dev else 0,
                         "ageDays": round((now - dev.first_seen) / 86_400, 1) if dev else None,
                         "bankRegistry": t.device_first_seen is not None, "known": device_known,
                         "keyBound": bool(dev and dev.public_jwk)}

    nets = s.execute(select(db.KnownNetwork).where(db.KnownNetwork.customer_id == customer.id)).scalars().all()
    same_net = any(n.network == place.network for n in nets)
    same_place = place.place_key is not None and any(n.place_key == place.place_key for n in nets)
    location_known = same_net or same_place
    derived["location"] = {"geoip": geo.geoip_available(), "country": place.country,
                           "knownNetwork": same_net, "knownPlace": same_place,
                           "privateAddress": place.private}

    local = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(ZoneInfo(policy.timezone))
    hist = customer.hour_histogram or [0] * 24
    if sum(hist) >= HOUR_HISTORY_MIN:
        dev_h = _hour_deviation(local.hour, hist)
        hour_unusual = dev_h >= HOUR_DEVIATION_UNUSUAL
        derived["hour"] = {"basis": "personal", "localHour": local.hour, "deviation": round(dev_h, 2)}
    else:
        hour_unusual = policy.quietHours.contains(local.hour)
        derived["hour"] = {"basis": "bank quiet hours", "localHour": local.hour}

    if t.payee_created_at is not None:
        age_days = int(max(0.0, now - t.payee_created_at) // 86_400)
        basis = "bank registry"
    else:
        kp = s.execute(select(db.KnownPayee).where(db.KnownPayee.customer_id == customer.id,
                                                   db.KnownPayee.payee_key == pkey)).scalars().first()
        age_days = int((now - kp.first_seen) // 86_400) if kp else 0
        basis = "authorised history" if kp else "never paid before"
    new_payee = age_days < policy.newPayeeDays if t.type in (TxnType.TRANSFER, TxnType.PAYMENT,
                                                             TxnType.ADD_BENEFICIARY) else False
    derived["payee"] = {"basis": basis, "ageDays": age_days, "new": new_payee}

    dormant = bool(customer.last_verified_at and
                   now - customer.last_verified_at > policy.dormancyDays * 86_400)

    location_key = place.place_key or f"net:{place.network}"
    facts = {"tenantId": tenant.id, "userId": customer.id, "action": _ueba_action(t.type),
             "amount": t.amount_minor / 100.0, "payeeKey": pkey, "deviceKey": dkey,
             "locationKey": location_key, "channel": t.channel,
             "occurredAt": datetime.fromtimestamp(now, tz=timezone.utc).isoformat()}
    body = dict(facts)
    if t.session:
        body["session"] = t.session
    raw = factors.ueba("/risk/preview", body)
    if raw is None:
        preview = {"band": "HIGH", "status": "UNAVAILABLE", "source": "unavailable",
                   "dimensionsUsed": [], "latencyMs": 0}
    else:
        preview = {**raw, "source": "live"}
    status = preview.get("status", "OK")
    pband = Band(preview.get("band", "HIGH"))
    if status == "INSUFFICIENT_DATA":
        pband = policy.coldStartBehaviouralBand
    behavioural = BehaviouralCtx(band=pband, score=None if preview.get("riskScore") is None
                                 else round(1 - float(preview["riskScore"]), 4),
                                 status=status, dimensionsUsed=preview.get("dimensionsUsed", []),
                                 source=preview.get("source", "live"),
                                 latencyMs=int(preview.get("latencyMs", 0)), dormant=dormant)

    cap = t.capability or {}
    capability = Capability(camera=bool(cap.get("camera", True)),
                            microphone=bool(cap.get("microphone", True)),
                            pointer=bool(cap.get("pointer", t.channel != "MOBILE")))

    segment = Segment(customer.segment)
    floor, org = policy.derive(t.type, band, t.amount_minor, t.currency, segment, new_payee)

    ctx = Context(
        userId=customer.id, transactionId=txn_id,
        transaction=TransactionCtx(amountBand=band, isNewBeneficiary=new_payee,
                                   beneficiaryAgeDays=age_days, type=t.type),
        device=DeviceCtx(deviceKnown=device_known, locationKnown=location_known,
                         hourUnusual=hour_unusual, channel=t.channel),
        behavioural=behavioural,
        enrollment=enrollment_of(s, customer),
        capability=capability,
        policy=Policy(actionFloor=floor, guidance=policy.agent.guidance if policy.agent.enabled else None),
        org=org,
    )
    facts["network"] = place.network
    facts["placeKey"] = place.place_key
    return Assembled(ctx=ctx, preview=preview, facts=facts, derived=derived, policy=policy)


def _ueba_action(t: TxnType) -> str:
    return {TxnType.TRANSFER: "TRANSFER", TxnType.PAYMENT: "PAYMENT",
            TxnType.CARD_NOT_PRESENT: "PAYMENT", TxnType.ADD_BENEFICIARY: "ADD_BENEFICIARY",
            TxnType.CHANGE_LIMIT: "CHANGE_LIMIT"}.get(t, "LOGIN")
