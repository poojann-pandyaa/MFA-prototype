from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request

from amfa import config
from amfa.common import crypto, signing
from amfa.common.web import ApiError, rate_limit
from amfa.core import db

SCOPES = {"users:write", "users:read", "transactions:write", "transactions:read",
          "proofs:verify", "policy:read", "policy:write", "events:read", "webhooks:write"}
DEFAULT_SCOPES = ["users:write", "users:read", "transactions:write", "transactions:read",
                  "proofs:verify", "events:read"]

ROLES = {
    "platform_admin": {"tenants:manage", "operators:manage", "keys:manage", "policy:write",
                       "policy:read", "review:decide", "events:read", "audit:read", "customers:manage"},
    "tenant_admin":   {"operators:manage", "keys:manage", "policy:write", "policy:read",
                       "events:read", "audit:read", "customers:manage"},
    "compliance":     {"policy:write", "policy:read", "events:read", "audit:read", "review:decide"},
    "analyst":        {"policy:read", "events:read", "review:decide"},
    "auditor":        {"policy:read", "events:read", "audit:read"},
}

OPERATOR_IDLE_S = 15 * 60
OPERATOR_ABSOLUTE_S = 8 * 3600
PASSWORD_STAGE_S = 5 * 60
LOCKOUT_AFTER = 5
LOCKOUT_S = 15 * 60
COOKIE = "amfa_op"


@dataclass
class TenantCaller:
    tenant: db.Tenant
    key_id: str
    scopes: set[str]


async def tenant_caller(request: Request) -> TenantCaller:
    body = await request.body()
    try:
        sreq = signing.parse_headers(request.headers)
    except signing.SignatureError as exc:
        raise ApiError(401, exc.code, "request signature required") from None
    rate_limit(f"ip:{request.state.client_ip}:tenant-api", 1200, 60)
    with db.session() as s:
        key = s.get(db.ApiKey, sreq.key_id)
        if key is None or key.status != "ACTIVE":
            crypto.burn_password_check("x")
            raise ApiError(401, "KEY_INVALID", "unknown or revoked API key")
        tenant = s.get(db.Tenant, key.tenant_id)
        if tenant is None or tenant.status != "ACTIVE":
            raise ApiError(403, "TENANT_SUSPENDED", "tenant is not active")
        secret = crypto.open_(key.secret_sealed, f"apikey|{key.id}", "apikey").decode()
        try:
            signing.verify(sreq, secret, request.method, request.url.path, request.url.query, body,
                           replay_scope="tenant")
        except signing.SignatureError as exc:
            raise ApiError(401, exc.code, "request signature invalid") from None
        rate_limit(f"key:{sreq.key_id}", 600, 60)
        if (key.last_used_at or 0) < time.time() - 60:
            key.last_used_at = time.time()
        return TenantCaller(tenant=tenant, key_id=key.id, scopes=set(key.scopes))


def require_scope(scope: str):
    async def dep(caller: TenantCaller = Depends(tenant_caller)) -> TenantCaller:
        if scope not in caller.scopes:
            raise ApiError(403, "SCOPE_REQUIRED", f"this key lacks the {scope} scope")
        return caller
    return dep


@dataclass
class ClientCaller:
    kind: str
    tenant_id: str
    customer_id: str
    session_id: Optional[str]
    scope: list
    token_id: str = ""

    @property
    def may_replace(self) -> bool:
        return "REPLACE" in self.scope


def client_caller(kind: str):
    def dep(request: Request) -> ClientCaller:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError(401, "TOKEN_MISSING", "bearer token required")
        token = auth[7:].strip()
        rate_limit(f"ip:{request.state.client_ip}:client", 240, 60)
        with db.session() as s:
            row = s.get(db.ClientToken, crypto.hash_token(token))
            if row is None or row.revoked or row.kind != kind or row.expires_at < time.time():
                raise ApiError(401, "TOKEN_INVALID", "token invalid or expired")
            return ClientCaller(kind=row.kind, tenant_id=row.tenant_id, customer_id=row.customer_id,
                                session_id=row.session_id, scope=list(row.scope or []), token_id=row.id)
    return dep


@dataclass
class OperatorCaller:
    operator: db.Operator
    session_id: str
    permissions: set[str]

    @property
    def tenant_id(self) -> Optional[str]:
        return self.operator.tenant_id

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def scope_tenant(self, tenant_id: Optional[str]) -> str:
        if self.operator.tenant_id:
            if tenant_id and tenant_id != self.operator.tenant_id:
                raise ApiError(403, "TENANT_SCOPE", "outside your organisation")
            return self.operator.tenant_id
        if not tenant_id:
            raise ApiError(400, "TENANT_REQUIRED", "platform administrators must name a tenant")
        return tenant_id


def new_operator_session(s, operator: db.Operator, stage: str) -> tuple[str, str]:
    cookie = crypto.token("os", 32)
    csrf = crypto.token("csrf", 24)
    ttl = PASSWORD_STAGE_S if stage == "PASSWORD" else OPERATOR_ABSOLUTE_S
    s.add(db.OperatorSession(id=crypto.hash_token(cookie), operator_id=operator.id,
                             csrf_hash=crypto.hash_token(csrf), stage=stage,
                             expires_at=time.time() + ttl))
    return cookie, csrf


def operator_caller(stage: str = "FULL", perm: Optional[str] = None):
    def dep(request: Request) -> OperatorCaller:
        cookie = request.cookies.get(COOKIE)
        if not cookie:
            raise ApiError(401, "NOT_SIGNED_IN", "sign in required")
        now = time.time()
        with db.session() as s:
            row = s.get(db.OperatorSession, crypto.hash_token(cookie))
            if row is None or row.expires_at < now or now - row.last_seen_at > OPERATOR_IDLE_S:
                raise ApiError(401, "SESSION_EXPIRED", "session expired; sign in again")
            if row.stage != stage:
                raise ApiError(401, "SECOND_FACTOR_REQUIRED" if row.stage == "PASSWORD"
                               else "WRONG_STAGE", "complete sign-in first")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                csrf = request.headers.get("x-csrf-token", "")
                if not csrf or not crypto.consteq(crypto.hash_token(csrf), row.csrf_hash):
                    raise ApiError(403, "CSRF_FAILED", "missing or invalid CSRF token")
            op = s.get(db.Operator, row.operator_id)
            if op is None or op.status != "ACTIVE":
                raise ApiError(401, "OPERATOR_DISABLED", "account disabled")
            row.last_seen_at = now
            perms = ROLES.get(op.role, set())
            if perm and perm not in perms:
                raise ApiError(403, "PERMISSION_REQUIRED", f"your role cannot {perm}")
            return OperatorCaller(operator=op, session_id=row.id, permissions=perms)
    return dep


def set_cookie(response, cookie: str, max_age: int) -> None:
    response.set_cookie(COOKIE, cookie, max_age=max_age, httponly=True,
                        secure=config.get().production, samesite="strict", path="/")
