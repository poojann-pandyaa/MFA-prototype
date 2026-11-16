from __future__ import annotations

import json
import time
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator, Optional

from sqlalchemy import (JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint, create_engine, event, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from amfa import config

SCHEMA_VERSION = 5


def now() -> float:
    return time.time()


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class SchemaMeta(Base):
    __tablename__ = "schema_meta"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(48), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    policy: Mapped[dict] = mapped_column(JSON)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class PolicyVersion(Base):
    __tablename__ = "policy_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("tenant_id", "version"),)


class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    secret_sealed: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_used_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    revoked_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Webhook(Base):
    __tablename__ = "webhook"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True)
    url: Mapped[str] = mapped_column(String(512))
    secret_sealed: Mapped[str] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class WebhookDelivery(Base):
    __tablename__ = "webhook_delivery"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    webhook_id: Mapped[str] = mapped_column(ForeignKey("webhook.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    last_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Operator(Base):
    __tablename__ = "operator"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tenant.id"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    totp_sealed: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    totp_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int] = mapped_column(BigInteger, default=0)
    role: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_login_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class OperatorSession(Base):
    __tablename__ = "operator_session"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("operator.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_seen_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float] = mapped_column(Float)


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True)
    external_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    segment: Mapped[str] = mapped_column(String(24), default="STANDARD")
    contact_sealed: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    consent: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    locked_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_verified_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verified_count: Mapped[int] = mapped_column(Integer, default=0)
    hour_histogram: Mapped[list] = mapped_column(JSON, default=lambda: [0] * 24)
    __table_args__ = (UniqueConstraint("tenant_id", "external_ref"),)


class BypassLimit(Base):
    """A customer's own "no extra checks up to this amount" setting. A raise waits in pending_* until its cooling-off ends."""
    __tablename__ = "bypass_limit"
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), primary_key=True)
    limit_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    pending_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    pending_from: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class TrustedHelper(Base):
    """A family member's phone the customer chose to let help with payments (optional). Created as an invite the customer shows on
    their own phone; the helper's phone redeems it, which sets device_key. It counts from active_from (after the cooling-off).
    Holds a label the customer typed and a keyed hash of the device, nothing else."""
    __tablename__ = "trusted_helper"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    label: Mapped[str] = mapped_column(String(24))
    invite_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    invite_expires: Mapped[float] = mapped_column(Float, default=0.0)
    device_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    active_from: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class CustomerPhase(Base):
    """Whether the model knows this customer yet. `evidence` is derived marks only (see engine/risk/readiness.Tracker), never
    transactions. Trimmed with the retention window."""
    __tablename__ = "customer_phase"
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(10), default="LEARNING")
    ready_since: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    verified_payments: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class CustomerProfile(Base):
    """What the customer chose to tell us at sign-up (bands, never exact figures), so the model has something to compare with before
    it has any history. Optional; erased with the customer. `regular_payees` are payees the bank vouches for (it checked the name)."""
    __tablename__ = "customer_profile"
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), primary_key=True)
    month_band: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    typical_band: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    regular_payees: Mapped[list] = mapped_column(JSON, default=list)       # hashed payee keys
    declared_at: Mapped[float] = mapped_column(Float, default=now)


class PeerGroupStat(Base):
    """How customers who answered the same way actually pay, once enough of them are known (see core/peers.py)."""
    __tablename__ = "peer_group_stat"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    group_key: Mapped[str] = mapped_column(String(40))
    n_customers: Mapped[int] = mapped_column(Integer)
    centre_log: Mapped[float] = mapped_column(Float)
    spread_log: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("tenant_id", "group_key"),)


class Enrollment(Base):
    __tablename__ = "enrollment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    factor: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="ENROLLED")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    enrolled_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("customer_id", "factor"),)


class TotpCredential(Base):
    __tablename__ = "totp_credential"
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), primary_key=True)
    secret_sealed: Mapped[str] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_step: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class ClientToken(Base):
    __tablename__ = "client_token"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    scope: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[float] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Device(Base):
    __tablename__ = "device"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    device_key: Mapped[str] = mapped_column(String(64))
    public_jwk: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    first_seen: Mapped[float] = mapped_column(Float, default=now)
    last_seen: Mapped[float] = mapped_column(Float, default=now)
    verified_count: Mapped[int] = mapped_column(Integer, default=0)
    bank_first_seen: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    platform: Mapped[str] = mapped_column(String(12), default="web")
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (UniqueConstraint("customer_id", "device_key"),)


class KnownNetwork(Base):
    __tablename__ = "known_network"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    network: Mapped[str] = mapped_column(String(64))
    place_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    first_seen: Mapped[float] = mapped_column(Float, default=now)
    last_seen: Mapped[float] = mapped_column(Float, default=now)
    count: Mapped[int] = mapped_column(Integer, default=0)


class KnownPayee(Base):
    __tablename__ = "known_payee"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    payee_key: Mapped[str] = mapped_column(String(64), index=True)
    first_seen: Mapped[float] = mapped_column(Float, default=now)
    count: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("customer_id", "payee_key"),)


class Transaction(Base):
    __tablename__ = "txn"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(24))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    payee_ref: Mapped[str] = mapped_column(String(64))
    payee_display: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),
                      Index("ix_txn_tenant_ext", "tenant_id", "external_id"))


class Decision(Base):
    __tablename__ = "decision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(40), index=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    transaction_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    context: Mapped[dict] = mapped_column(JSON)
    preview: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16))
    agent_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    agent_error: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    floor: Mapped[str] = mapped_column(String(4))
    floor_applied: Mapped[bool] = mapped_column(Boolean)
    risk_band: Mapped[str] = mapped_column(String(8))
    escalate: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    reasoning: Mapped[str] = mapped_column(Text)
    counterfactual: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer)
    decided_at: Mapped[float] = mapped_column(Float, default=now, index=True)


class AuthSession(Base):
    __tablename__ = "auth_session"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("txn.id"), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    checklist: Mapped[list] = mapped_column(JSON)
    escalations: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON)
    risk_band: Mapped[str] = mapped_column(String(8))
    device_bound: Mapped[bool] = mapped_column(Boolean, default=False)
    pad_flags: Mapped[int] = mapped_column(Integer, default=0)
    device_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    # set when the engine wants a person to decide: the customer still does the checks they can, then
    # the request goes to the review queue for this reason instead of being authorised
    review_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    expires_at: Mapped[float] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    resolved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}


class Challenge(Base):
    __tablename__ = "challenge"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("auth_session.id"), index=True)
    factor: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[float] = mapped_column(Float)
    consumed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class OtpCode(Base):
    __tablename__ = "otp_code"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("auth_session.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[float] = mapped_column(Float)
    consumed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Attempt(Base):
    __tablename__ = "attempt"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("auth_session.id"), index=True)
    factor: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20))
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liveness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pad_verdict: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str] = mapped_column(String(80), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    at: Mapped[float] = mapped_column(Float, default=now)


class Proof(Base):
    __tablename__ = "proof"
    jti: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    transaction_id: Mapped[str] = mapped_column(String(40), unique=True)
    issued_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float)
    consumed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    consumed_by: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)


class SigningKey(Base):
    __tablename__ = "signing_key"
    kid: Mapped[str] = mapped_column(String(40), primary_key=True)
    private_sealed: Mapped[str] = mapped_column(Text)
    public_jwk: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    retired_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class SecurityEvent(Base):
    __tablename__ = "security_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(40), index=True, nullable=True)
    customer_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    severity: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(300))
    at: Mapped[float] = mapped_column(Float, default=now, index=True)


class ReviewCase(Base):
    __tablename__ = "review_case"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    session_id: Mapped[str] = mapped_column(String(40), unique=True)
    transaction_id: Mapped[str] = mapped_column(String(40))
    customer_id: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    first_approver: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    first_approved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    resolved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class RiskLabel(Base):
    """The truth about a transaction, which arrives after the decision: the bank's fraud or genuine
    report, or a manual-review verdict. Training joins these to `decision` by transaction id."""
    __tablename__ = "risk_label"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    transaction_id: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(24))
    reason: Mapped[Optional[str]] = mapped_column(String(280), nullable=True)
    reported_at: Mapped[float] = mapped_column(Float, default=now, index=True)


class LearningRow(Base):
    """One settled payment for the band switch, by amount band and small categories only (amfa/core/learning_store.py). It holds no
    customer, account, amount, payee, device, network, place, address or exact time, and its id is random so the order rows were
    written in says nothing. `link` (a keyed hash of the transaction id) exists only so a label arriving later can find its row;
    it and `link_expires_at` are set to null after 90 days, after which nothing can tie the row to a person. The allowed columns are
    pinned by a test: adding one means changing that test on purpose."""
    __tablename__ = "learning_row"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    band: Mapped[int] = mapped_column(Integer, index=True)              # 0..3, engine/risk/collection_bands.py
    band_vs_usual: Mapped[str] = mapped_column(String(12))              # NEW, LOWER, SAME, ONE_HIGHER, TWO_HIGHER
    payee_kind: Mapped[str] = mapped_column(String(8))                  # KNOWN, NEW, FLAGGED
    device_kind: Mapped[str] = mapped_column(String(8))                 # KNOWN, NEW, HELPER
    hour_bucket: Mapped[str] = mapped_column(String(10))                # NIGHT, MORNING, AFTERNOON, EVENING
    hard_sign: Mapped[bool] = mapped_column(Boolean)
    rules_level: Mapped[str] = mapped_column(String(2))                 # T0..T4, engine/band_switch.level_of
    model_level: Mapped[str] = mapped_column(String(2))
    mode: Mapped[str] = mapped_column(String(12))                       # COLLECTING, ELIGIBLE, MODEL when it was decided
    outcome: Mapped[str] = mapped_column(String(10))                    # COMPLETED, DECLINED, CANCELLED, EXPIRED
    label: Mapped[str] = mapped_column(String(8), default="UNKNOWN")    # UNKNOWN, GENUINE, FRAUD
    label_source: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    week_start: Mapped[str] = mapped_column(String(10), index=True)     # the Monday of its week, YYYY-MM-DD
    link: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    link_expires_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)   # a whole day, never a time


class BandGateState(Base):
    """Where one amount band of one bank stands on its way from the rules to the model (amfa/core/band_gate.py). `figures` holds only
    counts and rates from the last grading. Every change of `state` is also in the audit log."""
    __tablename__ = "band_gate"
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    band: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(12), default="COLLECTING")     # COLLECTING, ELIGIBLE, MODEL
    streak: Mapped[int] = mapped_column(Integer, default=0)                   # weekly gradings passed in a row
    counted_week: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)   # the week whose passing grading counted last
    evaluated_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    changed_at: Mapped[float] = mapped_column(Float, default=now)
    figures: Mapped[dict] = mapped_column(JSON, default=dict)
    losing: Mapped[int] = mapped_column(Integer, default=0)                   # on MODEL: weekly gradings lost in a row
    losing_week: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)


class BandSwitchRequest(Base):
    """A change that relaxes the band switch waits here for a second, different operator: moving a band to the model, or turning the
    switch off or asking it for less evidence. Requests expire unapproved. Both operators are in the audit log."""
    __tablename__ = "band_switch_request"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16))                         # BAND_TO_MODEL, POLICY
    band: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)             # POLICY: the new collection section
    status: Mapped[str] = mapped_column(String(12), default="PENDING")    # PENDING, APPROVED, CANCELLED
    requested_by: Mapped[str] = mapped_column(String(40))
    requested_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float] = mapped_column(Float)
    approved_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    approved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(40), index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON)
    at: Mapped[float] = mapped_column(Float)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


@lru_cache(maxsize=1)
def engine():
    url = config.get().database_url
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        config.get().data_dir.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=1800)
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return eng


@lru_cache(maxsize=1)
def _factory() -> sessionmaker:
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


UPGRADES = [
    (2, "review_case", "first_approver", "VARCHAR(40)"),
    (2, "review_case", "first_approved_at", "FLOAT"),
    (2, "auth_session", "device_checked", "BOOLEAN DEFAULT FALSE"),
    (3, "device", "platform", "VARCHAR(12) DEFAULT 'web'"),
    (3, "device", "sign_count", "BIGINT DEFAULT 0"),
    (4, "attempt", "detail", "JSON"),
    (5, "auth_session", "review_reason", "VARCHAR(300)"),
]


def migrate() -> None:
    from sqlalchemy import inspect, text
    eng = engine()
    Base.metadata.create_all(eng)
    with session() as s:
        row = s.get(SchemaMeta, "version")
        current = int(row.value) if row else SCHEMA_VERSION
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"database schema v{current} is newer than this build "
                               f"(v{SCHEMA_VERSION}); refusing to start")
    insp = inspect(eng)
    with eng.begin() as c:
        for ver, table, column, ddl in UPGRADES:
            if column in {col["name"] for col in insp.get_columns(table)}:
                continue
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    with session() as s:
        row = s.get(SchemaMeta, "version")
        if row is None:
            s.add(SchemaMeta(key="version", value=str(SCHEMA_VERSION)))
        else:
            row.value = str(SCHEMA_VERSION)


@contextmanager
def session() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def is_postgres() -> bool:
    return engine().dialect.name == "postgresql"


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def first(s: Session, stmt):
    return s.execute(stmt).scalars().first()


__all__ = [n for n in dir() if not n.startswith("_")] + ["select"]
