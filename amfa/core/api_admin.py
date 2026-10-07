from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from amfa import config
from amfa.common import crypto
from amfa.common.web import ApiError, rate_limit
from amfa.core import audit, db, factors, orchestrator, otp
from amfa.core.api_tenant import apply_policy, create_webhook, decision_rows
from amfa.core.auth import (COOKIE, DEFAULT_SCOPES, LOCKOUT_AFTER, LOCKOUT_S, OPERATOR_ABSOLUTE_S,
                            PASSWORD_STAGE_S, ROLES, SCOPES, OperatorCaller, new_operator_session,
                            operator_caller, set_cookie)
from amfa.engine import llm_prompt
from amfa.engine.agent import backend_status
from amfa.engine.context import (Band, BehaviouralCtx, Capability, Context, DeviceCtx, Enrollment,
                                 Policy, Segment, TransactionCtx, TxnType)
from amfa.engine.policy import TenantPolicy
from amfa.engine.rules import select_by_rules

router = APIRouter(prefix="/admin/api", tags=["admin"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    rate_limit(f"ip:{request.state.client_ip}:login", 20, 300)
    rate_limit(f"login:{body.email.lower()}", 10, 900)
    now = time.time()
    failure: Optional[ApiError] = None
    with db.session() as s:
        op = s.execute(select(db.Operator).where(db.Operator.email == body.email.lower())).scalars().first()
        if op is None:
            crypto.burn_password_check(body.password)
            failure = ApiError(401, "LOGIN_FAILED", "email or password is incorrect")
        elif op.locked_until > now:
            failure = ApiError(423, "LOCKED", "too many failed attempts; try again later")
        elif op.status != "ACTIVE" or not crypto.verify_password(op.password_hash, body.password):
            op.failed_logins += 1
            if op.failed_logins >= LOCKOUT_AFTER:
                op.locked_until, op.failed_logins = now + LOCKOUT_S, 0
                audit.record(f"operator:{op.id}", "operator.lockout", op.id, {}, op.tenant_id, s=s)
            failure = ApiError(401, "LOGIN_FAILED", "email or password is incorrect")
    if failure is not None:
        raise failure
    if not config.get().admin_totp:
        with db.session() as s:
            op = s.execute(select(db.Operator).where(db.Operator.email == body.email.lower())).scalars().first()
            if crypto.password_needs_rehash(op.password_hash):
                op.password_hash = crypto.hash_password(body.password)
            op.failed_logins, op.last_login_at = 0, now
            cookie, csrf = new_operator_session(s, op, "FULL")
            audit.record(f"operator:{op.id}", "operator.login", op.id, {"secondFactor": False}, op.tenant_id, s=s)
            me = _me(s, op)
        set_cookie(response, cookie, OPERATOR_ABSOLUTE_S)
        return {"stage": "DONE", "csrf": csrf, "me": me}
    with db.session() as s:
        op = s.execute(select(db.Operator).where(db.Operator.email == body.email.lower())).scalars().first()
        if crypto.password_needs_rehash(op.password_hash):
            op.password_hash = crypto.hash_password(body.password)
        cookie, csrf = new_operator_session(s, op, "PASSWORD")
        out: dict = {"stage": "TOTP"}
        if not op.totp_confirmed:
            secret = otp.new_secret()
            op.totp_sealed = crypto.seal(secret.encode(), f"optotp|{op.id}", "totp")
            uri = otp.provisioning_uri(secret, op.email, "AMFA Console")
            out = {"stage": "TOTP_SETUP", "otpauthUri": uri, "qrSvg": otp.qr_svg(uri), "secret": secret}
    out["csrf"] = csrf
    set_cookie(response, cookie, PASSWORD_STAGE_S)
    return out


class TotpCode(Strict):
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/login/totp")
def login_totp(body: TotpCode, request: Request, response: Response,
               caller: OperatorCaller = Depends(operator_caller(stage="PASSWORD"))):
    code = body.code
    rate_limit(f"optotp:{caller.operator.id}", 10, 300)
    failed = False
    with db.session() as s:
        op = s.get(db.Operator, caller.operator.id)
        secret = crypto.open_(op.totp_sealed, f"optotp|{op.id}", "totp").decode()
        step = otp.verify_totp(secret, code, op.totp_last_step)
        if step is None:
            failed = True
            op.failed_logins += 1
            if op.failed_logins >= LOCKOUT_AFTER:
                op.locked_until, op.failed_logins = time.time() + LOCKOUT_S, 0
                for sess_row in s.execute(select(db.OperatorSession).where(
                        db.OperatorSession.operator_id == op.id)).scalars():
                    s.delete(sess_row)
                audit.record(f"operator:{op.id}", "operator.lockout", op.id, {"stage": "totp"}, op.tenant_id, s=s)
    if failed:
        raise ApiError(401, "TOTP_INVALID", "that code is not valid")
    with db.session() as s:
        op = s.get(db.Operator, caller.operator.id)
        if op.locked_until > time.time():
            raise ApiError(423, "LOCKED", "too many failed attempts; try again later")
        first_setup = not op.totp_confirmed
        op.totp_confirmed, op.totp_last_step = True, step
        op.failed_logins, op.last_login_at = 0, time.time()
        old = s.get(db.OperatorSession, caller.session_id)
        if old:
            s.delete(old)
        cookie, csrf = new_operator_session(s, op, "FULL")
        audit.record(f"operator:{op.id}", "operator.login", op.id,
                     {"totpEnrolled": first_setup}, op.tenant_id, s=s)
        me = _me(s, op)
    set_cookie(response, cookie, OPERATOR_ABSOLUTE_S)
    return {"csrf": csrf, "me": me}


@router.post("/logout")
def logout(response: Response, caller: OperatorCaller = Depends(operator_caller())):
    with db.session() as s:
        row = s.get(db.OperatorSession, caller.session_id)
        if row:
            s.delete(row)
    response.delete_cookie(COOKIE, path="/")
    return {"signedOut": True}


def _me(s, op: db.Operator) -> dict:
    tenant = s.get(db.Tenant, op.tenant_id) if op.tenant_id else None
    tenants = ([{"id": tenant.id, "name": tenant.name}] if tenant else
               [{"id": t.id, "name": t.name} for t in s.execute(select(db.Tenant)).scalars()])
    return {"id": op.id, "email": op.email, "role": op.role, "tenantId": op.tenant_id,
            "permissions": sorted(ROLES.get(op.role, set())), "tenants": tenants}


@router.get("/me")
def me(caller: OperatorCaller = Depends(operator_caller())):
    with db.session() as s:
        return _me(s, caller.operator)


@router.get("/overview")
def overview(tenant: Optional[str] = None, caller: OperatorCaller = Depends(operator_caller(perm="events:read"))):
    tid = caller.scope_tenant(tenant)
    since = time.time() - 86_400
    with db.session() as s:
        by_status = dict(s.execute(select(db.Transaction.status, func.count()).where(
            db.Transaction.tenant_id == tid, db.Transaction.created_at >= since)
            .group_by(db.Transaction.status)).all())
        bands = dict(s.execute(select(db.Decision.risk_band, func.count()).where(
            db.Decision.tenant_id == tid, db.Decision.decided_at >= since)
            .group_by(db.Decision.risk_band)).all())
        sources = dict(s.execute(select(db.Decision.source, func.count()).where(
            db.Decision.tenant_id == tid, db.Decision.decided_at >= since)
            .group_by(db.Decision.source)).all())
        latency = s.execute(select(func.avg(db.Decision.latency_ms)).where(
            db.Decision.tenant_id == tid, db.Decision.decided_at >= since)).scalar()
        factor_rows = s.execute(select(db.Attempt.factor, db.Attempt.status, func.count())
                                .join(db.AuthSession, db.AuthSession.id == db.Attempt.session_id)
                                .where(db.AuthSession.tenant_id == tid, db.Attempt.at >= since)
                                .group_by(db.Attempt.factor, db.Attempt.status)).all()
        reviews = s.execute(select(func.count()).select_from(db.ReviewCase).where(
            db.ReviewCase.tenant_id == tid, db.ReviewCase.status == "OPEN")).scalar_one()
        high = s.execute(select(func.count()).select_from(db.SecurityEvent).where(
            db.SecurityEvent.tenant_id == tid, db.SecurityEvent.severity == "HIGH",
            db.SecurityEvent.at >= since)).scalar_one()
        customers = s.execute(select(func.count()).select_from(db.Customer).where(
            db.Customer.tenant_id == tid, db.Customer.status != "ERASED")).scalar_one()
    factors_out: dict[str, dict] = {}
    for f, st, n in factor_rows:
        factors_out.setdefault(f, {})[st] = n
    return {"windowHours": 24, "transactions": by_status, "riskBands": bands, "decisionSource": sources,
            "avgDecisionMs": round(latency or 0, 1), "factorOutcomes": factors_out,
            "openReviews": reviews, "highSeverityEvents": high, "customers": customers,
            "agent": backend_status(), "factorServices": factors.health()}


@router.get("/decisions")
def admin_decisions(tenant: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
                    transactionId: Optional[str] = None,
                    caller: OperatorCaller = Depends(operator_caller(perm="events:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        return {"decisions": decision_rows(s, tid, limit, transactionId)}


@router.get("/transactions")
def admin_transactions(tenant: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
                       caller: OperatorCaller = Depends(operator_caller(perm="events:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        rows = s.execute(select(db.Transaction).where(db.Transaction.tenant_id == tid)
                         .order_by(db.Transaction.created_at.desc()).limit(limit)).scalars().all()
        out = []
        for t in rows:
            sess = s.get(db.AuthSession, t.session_id) if t.session_id else None
            cust = s.get(db.Customer, t.customer_id)
            attempts = s.execute(select(db.Attempt).where(db.Attempt.session_id == t.session_id)
                                 .order_by(db.Attempt.id)).scalars().all() if t.session_id else []
            out.append({"id": t.id, "externalId": t.external_id, "type": t.type, "status": t.status,
                        "amount": orchestrator.format_amount(t.amount_minor, t.currency),
                        "channel": t.channel, "createdAt": int(t.created_at), "customerId": t.customer_id,
                        "customerRef": cust.external_ref if cust else None,
                        "attempts": [{"factor": a.factor, "status": a.status, "reason": a.reason,
                                      "score": a.score, "threshold": a.threshold,
                                      "liveness": a.liveness_score, "pad": a.pad_verdict,
                                      "detail": a.detail, "at": int(a.at)} for a in attempts],
                        "riskBand": sess.risk_band if sess else None,
                        "steps": [{"factor": i["factor"], "status": i["status"], "attempts": i["attempts"],
                                   "addedBy": i.get("addedBy")} for i in (sess.checklist if sess else [])],
                        "escalations": sess.escalations if sess else [],
                        "resolution": sess.resolution if sess else None})
        return {"transactions": out}


@router.get("/events")
def admin_events(tenant: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
                 caller: OperatorCaller = Depends(operator_caller(perm="events:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        rows = s.execute(select(db.SecurityEvent).where(db.SecurityEvent.tenant_id == tid)
                         .order_by(db.SecurityEvent.id.desc()).limit(limit)).scalars().all()
        return {"events": [{"id": r.id, "severity": r.severity, "kind": r.kind, "message": r.message,
                            "sessionId": r.session_id, "customerId": r.customer_id, "at": int(r.at)}
                           for r in rows]}


@router.get("/reviews")
def reviews(tenant: Optional[str] = None, status: str = Query("OPEN", pattern="^(OPEN|APPROVED|REJECTED)$"),
            caller: OperatorCaller = Depends(operator_caller(perm="events:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        rows = s.execute(select(db.ReviewCase).where(db.ReviewCase.tenant_id == tid,
                                                     db.ReviewCase.status == status)
                         .order_by(db.ReviewCase.created_at.desc()).limit(100)).scalars().all()
        out = []
        for r in rows:
            t = s.get(db.Transaction, r.transaction_id)
            c = s.get(db.Customer, r.customer_id)
            out.append({"id": r.id, "reason": r.reason, "status": r.status, "createdAt": int(r.created_at),
                        "transactionId": r.transaction_id, "customer": c.external_ref if c else None,
                        "customerLocked": bool(c and c.status == "LOCKED"),
                        "amount": orchestrator.format_amount(t.amount_minor, t.currency) if t else None,
                        "type": t.type if t else None, "resolvedBy": r.resolved_by,
                        "firstApprover": r.first_approver,
                        "note": r.resolution_note})
        return {"reviews": out}


class ReviewDecision(Strict):
    approve: bool
    note: str = Field(min_length=10, max_length=500)


@router.post("/reviews/{case_id}/decide")
def decide_review(case_id: str, body: ReviewDecision,
                  caller: OperatorCaller = Depends(operator_caller(perm="review:decide"))):
    with db.session() as s:
        case = s.get(db.ReviewCase, case_id)
        if case is None:
            raise ApiError(404, "REVIEW_NOT_FOUND", "no such case")
        caller.scope_tenant(case.tenant_id)
        return orchestrator.review_decision(s, case, body.approve, caller.operator, body.note)


@router.get("/customers")
def find_customer(externalRef: str = Query(max_length=64), tenant: Optional[str] = None,
                  caller: OperatorCaller = Depends(operator_caller(perm="events:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        c = s.execute(select(db.Customer).where(db.Customer.tenant_id == tid,
                                                db.Customer.external_ref == externalRef)).scalars().first()
        if c is None:
            raise ApiError(404, "CUSTOMER_NOT_FOUND", "no such customer")
        enr = s.execute(select(db.Enrollment).where(db.Enrollment.customer_id == c.id)).scalars().all()
        devices = s.execute(select(func.count()).select_from(db.Device).where(db.Device.customer_id == c.id)).scalar_one()
        return {"id": c.id, "externalRef": c.external_ref, "status": c.status, "lockedReason": c.locked_reason,
                "segment": c.segment, "verifiedTransactions": c.verified_count,
                "enrollment": {e.factor: e.status for e in enr}, "devices": devices,
                "consent": c.consent and {"version": c.consent.get("version"), "at": c.consent.get("at")}}


class CustomerAction(Strict):
    action: Literal["unlock", "suspend", "segment"]
    segment: Optional[Segment] = None
    note: str = Field(min_length=10, max_length=300)


@router.post("/customers/{customer_id}")
def customer_action(customer_id: str, body: CustomerAction,
                    caller: OperatorCaller = Depends(operator_caller(perm="customers:manage"))):
    with db.session() as s:
        c = s.get(db.Customer, customer_id)
        if c is None:
            raise ApiError(404, "CUSTOMER_NOT_FOUND", "no such customer")
        caller.scope_tenant(c.tenant_id)
        if c.status == "ERASED":
            raise ApiError(409, "CUSTOMER_ERASED", "an erased customer cannot be changed")
        if body.action == "unlock":
            c.status, c.locked_reason = "ACTIVE", None
        elif body.action == "suspend":
            c.status = "SUSPENDED"
        elif body.action == "segment" and body.segment:
            c.segment = body.segment.value
        audit.record(f"operator:{caller.operator.id}", f"customer.{body.action}", c.id,
                     {"note": body.note, "segment": body.segment.value if body.segment else None},
                     c.tenant_id, s=s)
        return {"id": c.id, "status": c.status, "segment": c.segment}


@router.get("/policy")
def admin_policy(tenant: Optional[str] = None, caller: OperatorCaller = Depends(operator_caller(perm="policy:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        t = s.get(db.Tenant, tid)
        history = s.execute(select(db.PolicyVersion).where(db.PolicyVersion.tenant_id == tid)
                            .order_by(db.PolicyVersion.version.desc()).limit(20)).scalars().all()
        return {"version": t.policy_version, "policy": t.policy,
                "defaults": TenantPolicy().model_dump(mode="json"),
                "history": [{"version": h.version, "by": h.created_by, "at": int(h.created_at)} for h in history]}


class AdminPolicy(Strict):
    expectedVersion: int
    policy: dict


@router.put("/policy")
def admin_put_policy(body: AdminPolicy, tenant: Optional[str] = None,
                     caller: OperatorCaller = Depends(operator_caller(perm="policy:write"))):
    tid = caller.scope_tenant(tenant)
    try:
        new = TenantPolicy.model_validate(body.policy)
    except ValueError as exc:
        raise ApiError(422, "POLICY_INVALID", str(exc)[:800]) from None
    return apply_policy(tid, new, f"operator:{caller.operator.id}", body.expectedVersion,
                        platform=caller.operator.role == "platform_admin")


class PromptPreview(Strict):
    amountBand: Band = Band.HIGH
    type: TxnType = TxnType.TRANSFER
    isNewBeneficiary: bool = True
    deviceKnown: bool = False
    locationKnown: bool = True
    hourUnusual: bool = False
    behaviouralBand: Band = Band.LOW
    segment: Segment = Segment.STANDARD
    payeeName: Optional[str] = Field(default=None, max_length=140)
    remarks: Optional[str] = Field(default=None, max_length=280)


@router.post("/prompt-preview")
def prompt_preview(body: PromptPreview, tenant: Optional[str] = None,
                   caller: OperatorCaller = Depends(operator_caller(perm="policy:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        pol = TenantPolicy.model_validate(s.get(db.Tenant, tid).policy)
    floor, org = pol.derive(body.type, body.amountBand, 0, pol.currency, body.segment, body.isNewBeneficiary)
    ctx = Context(userId="usr_preview", transactionId="txn_preview",
                  transaction=TransactionCtx(amountBand=body.amountBand, isNewBeneficiary=body.isNewBeneficiary,
                                             type=body.type),
                  device=DeviceCtx(deviceKnown=body.deviceKnown, locationKnown=body.locationKnown,
                                   hourUnusual=body.hourUnusual),
                  behavioural=BehaviouralCtx(band=body.behaviouralBand), enrollment=Enrollment(),
                  capability=Capability(), org=org,
                  policy=Policy(actionFloor=floor, guidance=pol.agent.guidance if pol.agent.enabled else None))
    rendered = llm_prompt.render(ctx, select_by_rules(ctx))
    submitted = [t for t in (body.payeeName, body.remarks) if t]
    leaked = [t for t in submitted if t.lower()[:24] in rendered.lower()]
    return {"system": llm_prompt.SYSTEM, "prompt": rendered, "submittedFreeText": len(submitted),
            "freeTextReachingModel": len(leaked), "floor": floor.value, "agent": backend_status()}


@router.get("/keys")
def list_keys(tenant: Optional[str] = None, caller: OperatorCaller = Depends(operator_caller(perm="keys:manage"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        rows = s.execute(select(db.ApiKey).where(db.ApiKey.tenant_id == tid)
                         .order_by(db.ApiKey.created_at.desc())).scalars().all()
        return {"keys": [{"id": k.id, "name": k.name, "scopes": k.scopes, "status": k.status,
                          "createdAt": int(k.created_at), "lastUsedAt": int(k.last_used_at) if k.last_used_at else None}
                         for k in rows], "availableScopes": sorted(SCOPES)}


class NewKey(Strict):
    name: str = Field(min_length=3, max_length=80, pattern=r"^[\w .-]+$")
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))


def create_api_key(tenant_id: str, name: str, scopes: list[str], actor: str) -> dict:
    bad = [x for x in scopes if x not in SCOPES]
    if bad:
        raise ApiError(422, "UNKNOWN_SCOPE", f"unknown scopes: {bad}")
    env = "live" if config.get().production else "test"
    key_id = f"ak_{env}_{uuid.uuid4().hex[:16]}"
    secret = crypto.token("sk", 32)
    with db.session() as s:
        s.add(db.ApiKey(id=key_id, tenant_id=tenant_id, name=name, scopes=sorted(set(scopes)),
                        secret_sealed=crypto.seal(secret.encode(), f"apikey|{key_id}", "apikey"),
                        created_by=actor))
        audit.record(actor, "apikey.create", key_id, {"scopes": sorted(set(scopes))}, tenant_id, s=s)
    return {"keyId": key_id, "secret": secret, "scopes": sorted(set(scopes)),
            "note": "the secret is shown once; store it in the bank's secret manager now"}


@router.post("/keys", status_code=201)
def new_key(body: NewKey, tenant: Optional[str] = None,
            caller: OperatorCaller = Depends(operator_caller(perm="keys:manage"))):
    return create_api_key(caller.scope_tenant(tenant), body.name, body.scopes, f"operator:{caller.operator.id}")


@router.delete("/keys/{key_id}")
def revoke_key(key_id: str, caller: OperatorCaller = Depends(operator_caller(perm="keys:manage"))):
    with db.session() as s:
        k = s.get(db.ApiKey, key_id)
        if k is None:
            raise ApiError(404, "KEY_NOT_FOUND", "no such key")
        caller.scope_tenant(k.tenant_id)
        k.status, k.revoked_at = "REVOKED", time.time()
        audit.record(f"operator:{caller.operator.id}", "apikey.revoke", key_id, {}, k.tenant_id, s=s)
    return {"revoked": True}


class AdminWebhook(Strict):
    url: str = Field(max_length=512)
    events: list[str] = Field(min_length=1)


@router.post("/webhooks", status_code=201)
def admin_webhook(body: AdminWebhook, tenant: Optional[str] = None,
                  caller: OperatorCaller = Depends(operator_caller(perm="keys:manage"))):
    return create_webhook(caller.scope_tenant(tenant), body.url, body.events, f"operator:{caller.operator.id}")


@router.delete("/webhooks/{hook_id}")
def admin_disable_webhook(hook_id: str, caller: OperatorCaller = Depends(operator_caller(perm="keys:manage"))):
    with db.session() as s:
        h = s.get(db.Webhook, hook_id)
        if h is None:
            raise ApiError(404, "WEBHOOK_NOT_FOUND", "no such webhook")
        caller.scope_tenant(h.tenant_id)
        h.active = False
        audit.record(f"operator:{caller.operator.id}", "webhook.disable", hook_id, {}, h.tenant_id, s=s)
    return {"disabled": True}


@router.get("/webhooks")
def admin_webhooks(tenant: Optional[str] = None, caller: OperatorCaller = Depends(operator_caller(perm="keys:manage"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        hooks = s.execute(select(db.Webhook).where(db.Webhook.tenant_id == tid)).scalars().all()
        recent = s.execute(select(db.WebhookDelivery).where(db.WebhookDelivery.tenant_id == tid)
                           .order_by(db.WebhookDelivery.created_at.desc()).limit(30)).scalars().all()
        return {"webhooks": [{"id": h.id, "url": h.url, "events": h.events, "active": h.active} for h in hooks],
                "deliveries": [{"id": d.id, "event": d.event_type, "status": d.status, "attempts": d.attempts,
                                "lastStatus": d.last_status, "lastError": d.last_error,
                                "at": int(d.created_at)} for d in recent]}


@router.get("/audit")
def audit_log(tenant: Optional[str] = None, limit: int = Query(100, ge=1, le=500),
              caller: OperatorCaller = Depends(operator_caller(perm="audit:read"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        rows = s.execute(select(db.AuditLog).where(db.AuditLog.tenant_id == tid)
                         .order_by(db.AuditLog.id.desc()).limit(limit)).scalars().all()
        return {"entries": [{"id": r.id, "at": int(r.at), "actor": r.actor, "action": r.action,
                             "target": r.target, "detail": r.detail, "hash": r.hash[:16]} for r in rows]}


@router.get("/audit/verify")
def audit_verify(caller: OperatorCaller = Depends(operator_caller(perm="audit:read"))):
    return audit.verify()


class NewTenant(Strict):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9-]{2,48}$")


def create_tenant(name: str, slug: str, actor: str) -> dict:
    tid = f"tn_{uuid.uuid4().hex[:16]}"
    policy = TenantPolicy().model_dump(mode="json")
    with db.session() as s:
        if s.execute(select(db.Tenant).where(db.Tenant.slug == slug)).scalars().first():
            raise ApiError(409, "SLUG_TAKEN", "that slug is in use")
        s.add(db.Tenant(id=tid, name=name, slug=slug, policy=policy))
        s.flush()
        s.add(db.PolicyVersion(tenant_id=tid, version=1, policy=policy, created_by=actor))
        audit.record(actor, "tenant.create", tid, {"name": name}, tid, s=s)
    return {"id": tid, "name": name, "slug": slug}


@router.post("/tenants", status_code=201)
def new_tenant(body: NewTenant, caller: OperatorCaller = Depends(operator_caller(perm="tenants:manage"))):
    return create_tenant(body.name, body.slug, f"operator:{caller.operator.id}")


class NewOperator(Strict):
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["tenant_admin", "compliance", "analyst", "auditor"]
    password: str = Field(min_length=14, max_length=256)


def create_operator(email: str, role: str, password: str, tenant_id: Optional[str], actor: str) -> dict:
    if len(set(password)) < 6:
        raise ApiError(422, "PASSWORD_WEAK", "choose a less repetitive password")
    oid = f"op_{uuid.uuid4().hex[:16]}"
    with db.session() as s:
        if s.execute(select(db.Operator).where(db.Operator.email == email.lower())).scalars().first():
            raise ApiError(409, "EMAIL_TAKEN", "an operator with that e-mail exists")
        s.add(db.Operator(id=oid, tenant_id=tenant_id, email=email.lower(),
                          password_hash=crypto.hash_password(password), role=role))
        audit.record(actor, "operator.create", oid, {"role": role}, tenant_id, s=s)
    return {"id": oid, "email": email.lower(), "role": role,
            "note": "the operator enrols an authenticator at first sign-in"}


@router.get("/operators")
def list_operators(tenant: Optional[str] = None,
                   caller: OperatorCaller = Depends(operator_caller(perm="operators:manage"))):
    tid = caller.scope_tenant(tenant)
    with db.session() as s:
        rows = s.execute(select(db.Operator).where(db.Operator.tenant_id == tid)).scalars().all()
        return {"operators": [{"id": o.id, "email": o.email, "role": o.role, "status": o.status,
                               "totpEnrolled": o.totp_confirmed, "locked": o.locked_until > time.time(),
                               "lastLoginAt": int(o.last_login_at) if o.last_login_at else None} for o in rows]}


class OperatorAction(Strict):
    action: Literal["disable", "enable", "unlock", "reset_totp"]


@router.post("/operators/{operator_id}")
def operator_action(operator_id: str, body: OperatorAction,
                    caller: OperatorCaller = Depends(operator_caller(perm="operators:manage"))):
    if operator_id == caller.operator.id:
        raise ApiError(409, "SELF_ACTION", "you cannot change your own account")
    with db.session() as s:
        o = s.get(db.Operator, operator_id)
        if o is None or o.role == "platform_admin":
            raise ApiError(404, "OPERATOR_NOT_FOUND", "no such operator")
        caller.scope_tenant(o.tenant_id)
        if body.action == "disable":
            o.status = "DISABLED"
            for sess in s.execute(select(db.OperatorSession).where(db.OperatorSession.operator_id == o.id)).scalars():
                s.delete(sess)
        elif body.action == "enable":
            o.status = "ACTIVE"
        elif body.action == "unlock":
            o.locked_until, o.failed_logins = 0.0, 0
        elif body.action == "reset_totp":
            o.totp_sealed, o.totp_confirmed, o.totp_last_step = None, False, 0
        audit.record(f"operator:{caller.operator.id}", f"operator.{body.action}", o.id, {}, o.tenant_id, s=s)
        return {"id": o.id, "status": o.status}


@router.post("/operators", status_code=201)
def new_operator(body: NewOperator, tenant: Optional[str] = None,
                 caller: OperatorCaller = Depends(operator_caller(perm="operators:manage"))):
    tid = caller.scope_tenant(tenant)
    return create_operator(body.email, body.role, body.password, tid, f"operator:{caller.operator.id}")
