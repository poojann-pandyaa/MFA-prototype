from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from amfa import config
from amfa.common import crypto
from amfa.common.contract import ID_PATTERN
from amfa.common.web import ApiError
from amfa.core import audit, db, factors, orchestrator, proofs, webhooks
from amfa.core.auth import TenantCaller, require_scope
from amfa.core.context_builder import TxnInput, assemble, device_key
from amfa.engine.agent import build_agent
from amfa.engine.context import Segment, TxnType
from amfa.engine.decide import MonotonicityViolation, decide
from amfa.engine.policy import TenantPolicy
from amfa.engine.rationale import REASON_TEXT, customer_notice

router = APIRouter(prefix="/v1", tags=["tenant"])

REF = Field(pattern=ID_PATTERN)
ENROLLABLE = ["OTP", "FACE", "VOICE", "UEBA"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Contact(Strict):
    channel: Literal["sms", "email"]
    to: str = Field(min_length=5, max_length=254, pattern=r"^[A-Za-z0-9+@._-]+$")


class CreateUser(Strict):
    externalRef: str = REF
    segment: Segment = Segment.STANDARD
    contact: Optional[Contact] = None


class PatchUser(Strict):
    segment: Optional[Segment] = None
    contact: Optional[Contact] = None
    status: Optional[Literal["ACTIVE", "SUSPENDED"]] = None


class EnrollLink(Strict):
    factors: list[Literal["OTP", "FACE", "VOICE", "UEBA"]] = Field(default_factory=lambda: list(ENROLLABLE))
    ttlSeconds: int = Field(default=3600, ge=300, le=86_400)
    replace: bool = False


class BankDevice(Strict):
    deviceId: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    firstSeenAt: float = Field(gt=946684800)


def _user_view(s, c: db.Customer) -> dict:
    rows = s.execute(select(db.Enrollment).where(db.Enrollment.customer_id == c.id)).scalars().all()
    totp = s.get(db.TotpCredential, c.id)
    return {"id": c.id, "externalRef": c.external_ref, "status": c.status, "segment": c.segment,
            "lockedReason": c.locked_reason, "createdAt": int(c.created_at),
            "enrollment": {e.factor: e.status for e in rows}
                          | {"OTP_DELIVERY": bool(c.contact_sealed), "OTP_TOTP": bool(totp and totp.confirmed)},
            "verifiedTransactions": c.verified_count}


def _customer(s, tenant_id: str, user_id: str) -> db.Customer:
    c = s.get(db.Customer, user_id)
    if c is None or c.tenant_id != tenant_id or c.status == "ERASED":
        raise ApiError(404, "USER_NOT_FOUND", "no such user")
    return c


@router.post("/users", status_code=201)
def create_user(body: CreateUser, caller: TenantCaller = Depends(require_scope("users:write"))):
    with db.session() as s:
        existing = s.execute(select(db.Customer).where(db.Customer.tenant_id == caller.tenant.id,
                                                       db.Customer.external_ref == body.externalRef)).scalars().first()
        if existing is not None:
            return _user_view(s, existing)
        c = db.Customer(id=f"usr_{uuid.uuid4().hex[:20]}", tenant_id=caller.tenant.id,
                        external_ref=body.externalRef, segment=body.segment.value)
        if body.contact:
            c.contact_sealed = crypto.seal(body.contact.model_dump_json().encode(), f"contact|{c.id}", "contact")
        s.add(c)
        s.flush()
        view = _user_view(s, c)
    audit.record(f"apikey:{caller.key_id}", "customer.create", view["id"], {"segment": body.segment.value},
                 caller.tenant.id)
    return view


@router.get("/users")
def find_user(externalRef: str = Query(pattern=ID_PATTERN),
              caller: TenantCaller = Depends(require_scope("users:read"))):
    with db.session() as s:
        c = s.execute(select(db.Customer).where(db.Customer.tenant_id == caller.tenant.id,
                                                db.Customer.external_ref == externalRef)).scalars().first()
        if c is None or c.status == "ERASED":
            raise ApiError(404, "USER_NOT_FOUND", "no such user")
        return _user_view(s, c)


@router.get("/users/{user_id}")
def get_user(user_id: str, caller: TenantCaller = Depends(require_scope("users:read"))):
    with db.session() as s:
        return _user_view(s, _customer(s, caller.tenant.id, user_id))


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: PatchUser, caller: TenantCaller = Depends(require_scope("users:write"))):
    with db.session() as s:
        c = _customer(s, caller.tenant.id, user_id)
        changes: dict[str, Any] = {}
        if body.segment:
            c.segment = changes["segment"] = body.segment.value
        if body.contact:
            c.contact_sealed = crypto.seal(body.contact.model_dump_json().encode(), f"contact|{c.id}", "contact")
            changes["contact"] = "updated"
        if body.status:
            if c.status == "ERASED":
                raise ApiError(409, "CUSTOMER_ERASED", "an erased customer cannot be changed")
            if c.status == "LOCKED" and body.status == "ACTIVE":
                changes["unlocked"] = c.locked_reason
                c.locked_reason = None
            c.status = changes["status"] = body.status
        view = _user_view(s, c)
    audit.record(f"apikey:{caller.key_id}", "customer.update", user_id, changes, caller.tenant.id)
    return view


@router.delete("/users/{user_id}")
def erase_user(user_id: str, caller: TenantCaller = Depends(require_scope("users:write"))):
    with db.session() as s:
        c = _customer(s, caller.tenant.id, user_id)
        results = {f: factors.erase(f, caller.tenant.id, c.id) for f in ("FACE", "VOICE", "UEBA")}
        if not all(results.values()):
            raise ApiError(503, "ERASURE_INCOMPLETE", "a factor service could not be reached; retry")
        for model in (db.Enrollment, db.Device, db.KnownNetwork, db.KnownPayee):
            for row in s.execute(select(model).where(model.customer_id == c.id)).scalars():
                s.delete(row)
        totp = s.get(db.TotpCredential, c.id)
        if totp:
            s.delete(totp)
        c.status, c.contact_sealed, c.consent = "ERASED", None, None
        c.external_ref = f"erased:{uuid.uuid4().hex[:12]}"
        c.hour_histogram = [0] * 24
    audit.record(f"apikey:{caller.key_id}", "customer.erase", user_id, results, caller.tenant.id)
    return {"erased": True}


@router.post("/users/{user_id}/enrollment-links", status_code=201)
def enrollment_link(user_id: str, body: EnrollLink, caller: TenantCaller = Depends(require_scope("users:write"))):
    ttl = min(body.ttlSeconds, config.get().enrollment_link_ttl_max_s)
    permitted = {f.value for f in TenantPolicy.model_validate(caller.tenant.policy).permittedFactors}
    body.factors = [f for f in body.factors if f in permitted]
    if not body.factors:
        raise ApiError(400, "NO_PERMITTED_FACTORS", "none of the requested factors is permitted by policy")
    token = crypto.token("et", 32)
    with db.session() as s:
        c = _customer(s, caller.tenant.id, user_id)
        if c.status != "ACTIVE":
            raise ApiError(409, "USER_NOT_ACTIVE", f"user is {c.status}")
        s.add(db.ClientToken(id=crypto.hash_token(token), kind="ENROLL", tenant_id=caller.tenant.id,
                             customer_id=c.id, scope=list(dict.fromkeys(body.factors)) + (["REPLACE"] if body.replace else []),
                             expires_at=time.time() + ttl))
    audit.record(f"apikey:{caller.key_id}", "enrollment.link", user_id,
                 {"factors": body.factors, "replace": body.replace}, caller.tenant.id)
    return {"url": f"{config.get().public_url}/hosted/enroll?t={caller.tenant.slug}#token={token}", "token": token,
            "expiresAt": int(time.time() + ttl), "factors": body.factors}


@router.post("/users/{user_id}/devices", status_code=201)
def register_bank_device(user_id: str, body: BankDevice,
                         caller: TenantCaller = Depends(require_scope("users:write"))):
    with db.session() as s:
        c = _customer(s, caller.tenant.id, user_id)
        key = device_key(caller.tenant.id, body.deviceId)
        dev = s.execute(select(db.Device).where(db.Device.customer_id == c.id,
                                                db.Device.device_key == key)).scalars().first()
        if dev is None:
            s.add(db.Device(customer_id=c.id, device_key=key, first_seen=time.time(),
                            bank_first_seen=body.firstSeenAt))
        else:
            dev.bank_first_seen = body.firstSeenAt
    return {"registered": True}


class Payee(Strict):
    ref: str = REF
    createdAt: Optional[float] = Field(default=None, gt=946684800)
    display: Optional[str] = Field(default=None, max_length=40, pattern=r"^[\w .,'&()•*-]*$")
    name: Optional[str] = Field(default=None, max_length=140)


class DeviceInfo(Strict):
    deviceId: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    ip: str = Field(max_length=45)
    firstSeenAt: Optional[float] = Field(default=None, gt=946684800)


class CapabilityInfo(Strict):
    camera: bool = True
    microphone: bool = True
    pointer: Optional[bool] = None


class SessionInfo(Strict):
    start: float
    end: float
    actions: list[float] = Field(default_factory=list, max_length=2000)


class AccountEvents(Strict):
    simChangedAt: Optional[float] = Field(default=None, gt=946684800)
    contactChangedAt: Optional[float] = Field(default=None, gt=946684800)
    passwordResetAt: Optional[float] = Field(default=None, gt=946684800)
    payeeRiskFlag: bool = False


class InitiateTxn(Strict):
    userId: str = REF
    externalId: str = REF
    type: TxnType = TxnType.TRANSFER
    amountMinor: int = Field(ge=0, le=10**15)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    payee: Payee
    remarks: Optional[str] = Field(default=None, max_length=280)
    channel: Literal["MOBILE", "WEB", "BRANCH", "API"] = "MOBILE"
    device: DeviceInfo
    capability: Optional[CapabilityInfo] = None
    session: Optional[SessionInfo] = None
    accountEvents: Optional[AccountEvents] = None


def _txn_view(s, txn: db.Transaction, include_proof: bool = True) -> dict:
    out = {"transactionId": txn.id, "externalId": txn.external_id, "status": txn.status,
           "type": txn.type, "amountMinor": txn.amount_minor, "currency": txn.currency,
           "createdAt": int(txn.created_at), "sessionId": txn.session_id}
    sess = s.get(db.AuthSession, txn.session_id) if txn.session_id else None
    if sess:
        out["session"] = {"status": sess.status, "resolution": sess.resolution,
                          "checklist": [{"factor": i["factor"], "status": i["status"],
                                         "attempts": i["attempts"], "addedBy": i.get("addedBy")}
                                        for i in sess.checklist],
                          "escalations": sess.escalations, "expiresAt": int(sess.expires_at)}
    if include_proof and txn.status == "AUTHORIZED":
        p = s.execute(select(db.Proof).where(db.Proof.transaction_id == txn.id)).scalars().first()
        if p is not None and p.consumed_at is None and p.expires_at > time.time():
            out["proofAvailable"] = True
    return out


@router.post("/transactions", status_code=201)
def initiate(body: InitiateTxn, request: Request,
             idempotency_key: str = Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{8,64}$"),
             caller: TenantCaller = Depends(require_scope("transactions:write"))):
    tenant = caller.tenant
    policy = TenantPolicy.model_validate(tenant.policy)
    fingerprint = hashlib.sha256(json.dumps(
        body.model_dump(mode="json", exclude={"remarks": True, "payee": {"name"}}),
        sort_keys=True).encode()).hexdigest()

    with db.session() as s:
        prior = s.execute(select(db.Transaction).where(db.Transaction.tenant_id == tenant.id,
                                                       db.Transaction.idempotency_key == idempotency_key)).scalars().first()
        if prior is not None:
            if prior.request_hash != fingerprint:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "key reused with a different request")
            return {**_txn_view(s, prior), "replayed": True}

        customer = _customer(s, tenant.id, body.userId)
        if customer.status != "ACTIVE":
            raise ApiError(409, "USER_NOT_ACTIVE", f"user is {customer.status}")
        if body.currency != policy.currency and body.currency not in policy.amountBands:
            raise ApiError(422, "CURRENCY_NOT_CONFIGURED", "no amount bands for that currency")

        txn_id = f"txn_{uuid.uuid4().hex[:20]}"
        try:
            geo_ip = body.device.ip
            from amfa.common.geo import parse_ip
            parse_ip(geo_ip)
        except ValueError:
            raise ApiError(422, "IP_INVALID", "device.ip is not an IP address") from None

        cap = body.capability.model_dump(exclude_none=True) if body.capability else None
        assembled = assemble(s, tenant, customer, txn_id, TxnInput(
            type=body.type, amount_minor=body.amountMinor, currency=body.currency,
            payee_ref=body.payee.ref, channel=body.channel, device_id=body.device.deviceId,
            ip=body.device.ip, payee_created_at=body.payee.createdAt,
            device_first_seen=body.device.firstSeenAt, capability=cap,
            session=body.session.model_dump() if body.session else None,
            account_events=body.accountEvents.model_dump() if body.accountEvents else None))

        agent = build_agent() if policy.agent.enabled else None
        try:
            plan = decide(assembled.ctx, agent=agent, features=assembled.features)
        except MonotonicityViolation:
            plan = decide(assembled.ctx, agent=None, features=assembled.features)
            plan.agentError = "MonotonicityViolation: agent output discarded"

        facts = dict(assembled.facts)
        if body.session:
            facts["session"] = body.session.model_dump()
        txn = db.Transaction(id=txn_id, tenant_id=tenant.id, customer_id=customer.id,
                             external_id=body.externalId, idempotency_key=idempotency_key,
                             request_hash=fingerprint, type=body.type.value,
                             amount_minor=body.amountMinor, currency=body.currency,
                             payee_ref=body.payee.ref, payee_display=body.payee.display,
                             channel=body.channel, status="PENDING_AUTH", facts=facts)
        s.add(txn)
        try:
            s.flush()
        except IntegrityError:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "concurrent request with the same key") from None

        plan_json = plan.model_dump(mode="json")
        s.add(db.Decision(plan_id=plan.planId, tenant_id=tenant.id, transaction_id=txn.id,
                          customer_id=customer.id, context=assembled.ctx.model_dump(mode="json")
                          | {"_derived": assembled.derived, "_features": assembled.features},
                          preview={k: v for k, v in assembled.preview.items() if k != "userId"},
                          plan=plan_json, source=plan.source, agent_name=plan.agentName or None,
                          agent_error=plan.agentError, floor=plan.floor.value,
                          floor_applied=plan.policyFloorApplied, risk_band=plan.riskBand.value,
                          escalate=plan.escalate, reasoning=plan.reasoning,
                          counterfactual=plan.counterfactual, latency_ms=plan.latencyMs,
                          policy_version=tenant.policy_version))
        sess, client_token = orchestrator.create_session(s, tenant=tenant, txn=txn, customer=customer,
                                                         ctx=assembled.ctx, plan=plan, policy=policy)
        txn.session_id = sess.id
        if sess.status == orchestrator.REVIEW:
            txn.status = "REVIEW"
        view = _txn_view(s, txn, include_proof=False)
        view["plan"] = {"required": [f.value for f in plan.sequence()], "riskBand": plan.riskBand.value,
                        "threatLevel": plan.threatLevel, "riskScore": plan.riskScore,
                        "modelLevel": plan.modelLevel, "capReason": plan.capReason,
                        "riskReasons": plan.riskReasons, "modelVersion": plan.modelVersion,
                        "riskReasonText": [REASON_TEXT.get(k, k) for k, _ in plan.riskReasons],
                        "floor": plan.floor.value, "source": plan.source, "reasoning": plan.reasoning,
                        "escalate": plan.escalate, "rulesBaseline": [f.value for f in plan.rulesBaseline],
                        "agentAdded": [f.value for f in plan.agentAdded],
                        "orgAdded": [f.value for f in plan.orgAdded],
                        "customerNotice": customer_notice(plan, body.type)}
        if sess.status == orchestrator.PENDING:
            view["challengeUrl"] = f"{config.get().public_url}/hosted/challenge?t={tenant.slug}#token={client_token}"
            view["clientToken"] = client_token
        return view


@router.get("/transactions/{txn_id}")
def get_txn(txn_id: str, caller: TenantCaller = Depends(require_scope("transactions:read"))):
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        if txn is None or txn.tenant_id != caller.tenant.id:
            raise ApiError(404, "TXN_NOT_FOUND", "no such transaction")
        sess = s.get(db.AuthSession, txn.session_id) if txn.session_id else None
        if sess and sess.status == orchestrator.PENDING and sess.expires_at < time.time():
            orchestrator._expire(s, sess, txn)
        return _txn_view(s, txn)


@router.post("/transactions/{txn_id}/proof")
def fetch_proof(txn_id: str, caller: TenantCaller = Depends(require_scope("transactions:read"))):
    from sqlalchemy import delete
    policy = TenantPolicy.model_validate(caller.tenant.policy)
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        if txn is None or txn.tenant_id != caller.tenant.id:
            raise ApiError(404, "TXN_NOT_FOUND", "no such transaction")
        if txn.status != "AUTHORIZED":
            raise ApiError(409, "NOT_AUTHORIZED", f"transaction is {txn.status}")
        sess = s.get(db.AuthSession, txn.session_id)
        customer = s.get(db.Customer, txn.customer_id)
        if time.time() - (sess.resolved_at or 0) > policy.proofTtlSeconds * 5:
            raise ApiError(409, "PROOF_WINDOW_CLOSED", "authorisation too old; start a new transaction")
        existing = s.execute(select(db.Proof).where(db.Proof.transaction_id == txn.id)).scalars().first()
        if existing is not None:
            n = s.execute(delete(db.Proof).where(db.Proof.jti == existing.jti,
                                                 db.Proof.consumed_at.is_(None))).rowcount
            if n != 1:
                raise ApiError(409, "PROOF_CONSUMED", "proof already consumed")
        passed = [i["factor"] for i in sess.checklist if i["status"] == "PASSED"]
        review = s.execute(select(db.ReviewCase).where(db.ReviewCase.session_id == sess.id,
                                                       db.ReviewCase.status == "APPROVED")).scalars().first()
        review_by = f"{review.first_approver},{review.resolved_by}" if review else None
        return proofs.issue(tenant_id=caller.tenant.id, customer_ref=customer.external_ref, txn=txn,
                            session_id=sess.id, plan_id=sess.plan_id,
                            factors=passed + (["review"] if review else []),
                            risk_band=sess.risk_band, floor=sess.context["policy"]["actionFloor"],
                            ttl_s=policy.proofTtlSeconds, review_by=review_by, s=s)


@router.post("/transactions/{txn_id}/cancel")
def cancel(txn_id: str, caller: TenantCaller = Depends(require_scope("transactions:write"))):
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        if txn is None or txn.tenant_id != caller.tenant.id:
            raise ApiError(404, "TXN_NOT_FOUND", "no such transaction")
        if txn.status not in ("PENDING_AUTH", "REVIEW"):
            raise ApiError(409, "NOT_CANCELLABLE", f"transaction is {txn.status}")
        txn.status = "CANCELLED"
        sess = s.get(db.AuthSession, txn.session_id)
        if sess and sess.status in (orchestrator.PENDING, orchestrator.REVIEW):
            sess.status, sess.resolution, sess.resolved_at = orchestrator.BLOCKED, "CANCELLED", time.time()
        return _txn_view(s, txn, include_proof=False)


class Feedback(Strict):
    label: Literal["FRAUD", "GENUINE", "DISPUTED"]
    source: Literal["customer_report", "chargeback", "bank_ops"] = "customer_report"
    reason: Optional[str] = Field(default=None, max_length=280, pattern=r"^[^\x00-\x1f]*$")


@router.post("/transactions/{txn_id}/feedback", status_code=201)
def feedback(txn_id: str, body: Feedback, caller: TenantCaller = Depends(require_scope("transactions:write"))):
    """The bank reports what a transaction turned out to be ("this wasn't me", a chargeback, its own
    investigation). Stored as a training label; a fraud report also raises a security event."""
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        if txn is None or txn.tenant_id != caller.tenant.id:
            raise ApiError(404, "TXN_NOT_FOUND", "no such transaction")
        row = db.RiskLabel(tenant_id=txn.tenant_id, transaction_id=txn.id, label=body.label,
                           source=body.source, reason=body.reason)
        s.add(row)
        if body.label == "FRAUD":
            s.add(db.SecurityEvent(tenant_id=txn.tenant_id, customer_id=txn.customer_id,
                                   session_id=txn.session_id, severity="HIGH", kind="FRAUD_REPORTED",
                                   message=f"{body.source}: transaction {txn.external_id} reported as fraud"))
        audit.record(f"apikey:{caller.key_id}", "transaction.feedback", txn.id,
                     {"label": body.label, "source": body.source}, txn.tenant_id, s=s)
        s.flush()
        return {"transactionId": txn.id, "label": row.label, "source": row.source,
                "reportedAt": int(row.reported_at)}


class VerifyProof(Strict):
    token: str = Field(max_length=4096)
    userExternalRef: str = REF
    externalTransactionId: str = REF
    amountMinor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    payeeRef: str = REF
    type: TxnType = TxnType.TRANSFER
    consume: bool = True


@router.post("/proofs/verify")
def verify_proof(body: VerifyProof, caller: TenantCaller = Depends(require_scope("proofs:verify"))):
    return proofs.verify(body.token, tenant_id=caller.tenant.id, customer_ref=body.userExternalRef,
                         external_txn=body.externalTransactionId, amount_minor=body.amountMinor,
                         currency=body.currency, payee_ref=body.payeeRef, txn_type=body.type.value,
                         consume=body.consume, consumer=caller.key_id)


@router.get("/policy")
def get_policy(caller: TenantCaller = Depends(require_scope("policy:read"))):
    return {"version": caller.tenant.policy_version, "policy": caller.tenant.policy}


def apply_policy(tenant_id: str, new: TenantPolicy, actor: str, expected_version: Optional[int],
                 platform: bool = False) -> dict:
    with db.session() as s:
        t = s.get(db.Tenant, tenant_id)
        if expected_version is not None and expected_version != t.policy_version:
            raise ApiError(409, "POLICY_VERSION_CONFLICT", f"policy is at version {t.policy_version}")
        old = t.policy
        if not platform and new.regulatoryProfile.value != (old or {}).get("regulatoryProfile", "RBI_2025"):
            raise ApiError(403, "REGULATORY_PROFILE_LOCKED",
                           "the regulatory profile can only be changed by the platform operator")
        t.policy = new.model_dump(mode="json")
        t.policy_version += 1
        s.add(db.PolicyVersion(tenant_id=t.id, version=t.policy_version, policy=t.policy, created_by=actor))
        changed = sorted(k for k in t.policy if t.policy.get(k) != (old or {}).get(k))
        version = t.policy_version
    audit.record(actor, "policy.update", tenant_id, {"version": version, "changed": changed}, tenant_id)
    return {"version": version, "changed": changed}


class PutPolicy(Strict):
    expectedVersion: Optional[int] = None
    policy: dict


@router.put("/policy")
def put_policy(body: PutPolicy, caller: TenantCaller = Depends(require_scope("policy:write"))):
    try:
        new = TenantPolicy.model_validate(body.policy)
    except ValueError as exc:
        raise ApiError(422, "POLICY_INVALID", str(exc)[:500]) from None
    return apply_policy(caller.tenant.id, new, f"apikey:{caller.key_id}", body.expectedVersion)


class NewWebhook(Strict):
    url: str = Field(max_length=512)
    events: list[str] = Field(min_length=1, max_length=len(webhooks.EVENTS) + 1)


def create_webhook(tenant_id: str, url: str, events: list[str], actor: str) -> dict:
    bad = [e for e in events if e not in webhooks.EVENTS and e != "*"]
    if bad:
        raise ApiError(422, "UNKNOWN_EVENT", f"unknown events: {bad}")
    try:
        webhooks.check_url(url)
    except webhooks.UnsafeWebhook as exc:
        raise ApiError(422, "WEBHOOK_URL_REJECTED", str(exc)) from None
    secret = crypto.token("whsec", 32)
    hid = f"wh_{uuid.uuid4().hex[:16]}"
    with db.session() as s:
        s.add(db.Webhook(id=hid, tenant_id=tenant_id, url=url, events=events,
                         secret_sealed=crypto.seal(secret.encode(), f"webhook|{hid}", "webhook")))
    audit.record(actor, "webhook.create", hid, {"url": url, "events": events}, tenant_id)
    return {"id": hid, "url": url, "events": events, "secret": secret,
            "note": "the signing secret is shown once; store it now"}


@router.post("/webhooks", status_code=201)
def new_webhook(body: NewWebhook, caller: TenantCaller = Depends(require_scope("webhooks:write"))):
    return create_webhook(caller.tenant.id, body.url, body.events, f"apikey:{caller.key_id}")


@router.get("/webhooks")
def list_webhooks(caller: TenantCaller = Depends(require_scope("webhooks:write"))):
    with db.session() as s:
        rows = s.execute(select(db.Webhook).where(db.Webhook.tenant_id == caller.tenant.id)).scalars().all()
        return {"webhooks": [{"id": h.id, "url": h.url, "events": h.events, "active": h.active} for h in rows]}


@router.delete("/webhooks/{hook_id}")
def delete_webhook(hook_id: str, caller: TenantCaller = Depends(require_scope("webhooks:write"))):
    with db.session() as s:
        h = s.get(db.Webhook, hook_id)
        if h is None or h.tenant_id != caller.tenant.id:
            raise ApiError(404, "WEBHOOK_NOT_FOUND", "no such webhook")
        h.active = False
    audit.record(f"apikey:{caller.key_id}", "webhook.disable", hook_id, {}, caller.tenant.id)
    return {"disabled": True}


def decision_rows(s, tenant_id: str, limit: int, transaction_id: Optional[str] = None) -> list[dict]:
    q = select(db.Decision).where(db.Decision.tenant_id == tenant_id)
    if transaction_id:
        q = q.where(db.Decision.transaction_id == transaction_id)
    rows = s.execute(q.order_by(db.Decision.id.desc()).limit(limit)).scalars().all()
    return [{"planId": r.plan_id, "transactionId": r.transaction_id, "customerId": r.customer_id,
             "decidedAt": int(r.decided_at), "riskBand": r.risk_band, "floor": r.floor,
             "floorApplied": r.floor_applied, "source": r.source, "agent": r.agent_name,
             "agentError": r.agent_error, "escalate": r.escalate, "reasoning": r.reasoning,
             "counterfactual": r.counterfactual, "latencyMs": r.latency_ms,
             "required": r.plan.get("order") or r.plan.get("required"),
             "rulesBaseline": r.plan.get("rulesBaseline"), "agentAdded": r.plan.get("agentAdded"),
             "orgAdded": r.plan.get("orgAdded"), "context": r.context, "preview": r.preview,
             "policyVersion": r.policy_version} for r in rows]


@router.get("/decisions")
def decisions(limit: int = Query(50, ge=1, le=500), transactionId: Optional[str] = None,
              caller: TenantCaller = Depends(require_scope("events:read"))):
    with db.session() as s:
        return {"decisions": decision_rows(s, caller.tenant.id, limit, transactionId)}


@router.get("/security-events")
def security_events(limit: int = Query(50, ge=1, le=500),
                    caller: TenantCaller = Depends(require_scope("events:read"))):
    with db.session() as s:
        rows = s.execute(select(db.SecurityEvent).where(db.SecurityEvent.tenant_id == caller.tenant.id)
                         .order_by(db.SecurityEvent.id.desc()).limit(limit)).scalars().all()
        return {"events": [{"id": r.id, "severity": r.severity, "kind": r.kind, "message": r.message,
                            "sessionId": r.session_id, "customerId": r.customer_id, "at": int(r.at)}
                           for r in rows]}
