"""The band service: the band switch's anonymous rows, band states and candidate progress, in a database of its own.

    python -m amfa.cli serve bands            (port 8104; the core uses it when AMFA_BANDS_URL points here)

Only the core may call it: every request is signed with the internal token as the core (the same scheme as the factor services),
and a replayed request is refused. It sits on the internal network only. It holds nothing that identifies anyone: rows arrive
already anonymous, every request body is a strict model that refuses unknown fields, and a row's link is a keyed hash whose key
stays in the core. What a change needs the core to record (audit entries, security events, requests to cancel) is returned as
events; the core writes them into its own audit log.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from amfa import config
from amfa.bands import db as bdb
from amfa.bands import engine
from amfa.bands.engine import Events
from amfa.common import logs, signing, web
from amfa.common.web import ApiError
from amfa.engine.policy import CollectionPolicy

ID = Field(min_length=1, max_length=40)
VERSION = Field(default=None, max_length=64)
LEVEL = Field(pattern=r"^T[0-4]$")
SOURCE = r"^(customer_report|chargeback|bank_ops|review_verdict)$"


async def internal_auth(request: Request) -> None:
    body = await request.body()
    try:
        req = signing.parse_headers(request.headers)
        if req.key_id != "core":
            raise signing.SignatureError("UNKNOWN_CALLER")
        signing.verify(req, config.get().internal_token, request.method, request.url.path, request.url.query, body,
                       replay_scope="bands")
    except signing.SignatureError as exc:
        raise ApiError(401, exc.code, "internal authentication failed") from None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Row(Strict):
    """Exactly what a band row may carry. Anything else in the body, a customer or amount included, is refused."""
    tenant_id: str = ID
    band: int = Field(ge=0, le=3)
    band_vs_usual: str = Field(pattern=r"^(NEW|LOWER|SAME|ONE_HIGHER|TWO_HIGHER)$")
    payee_kind: str = Field(pattern=r"^(KNOWN|NEW|FLAGGED)$")
    device_kind: str = Field(pattern=r"^(KNOWN|NEW|HELPER)$")
    hour_bucket: str = Field(pattern=r"^(NIGHT|MORNING|AFTERNOON|EVENING)$")
    hard_sign: bool
    rules_level: str = LEVEL
    model_level: str = LEVEL
    model_version: Optional[str] = VERSION
    candidate_level: Optional[str] = Field(default=None, pattern=r"^T[0-4]$")
    candidate_version: Optional[str] = VERSION
    mode: str = Field(pattern=r"^(COLLECTING|ELIGIBLE|MODEL)$")
    outcome: str = Field(pattern=r"^(COMPLETED|DECLINED|CANCELLED|EXPIRED)$")
    label: str = Field(pattern=r"^(UNKNOWN|GENUINE|FRAUD)$")
    label_source: Optional[str] = Field(default=None, pattern=SOURCE)
    week_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    link: str = Field(pattern=r"^[0-9a-f]{64}$")
    link_expires_at: float


class AddRow(Strict):
    fields: Row
    now: float


class Label(Strict):
    link: str = Field(pattern=r"^[0-9a-f]{64}$")
    label: str = Field(pattern=r"^(FRAUD|GENUINE)$")
    source: str = Field(pattern=SOURCE)
    now: float


class Evaluate(Strict):
    tenantId: str = ID
    collection: dict
    timezone: str = Field(max_length=64)
    version: Optional[str] = VERSION
    candidate: Optional[str] = VERSION
    now: float


class TenantBand(Strict):
    tenantId: str = ID
    band: int = Field(ge=0, le=3)


class TenantNow(Strict):
    tenantId: str = ID
    now: float


class DecisionState(Strict):
    tenantId: str = ID
    band: int = Field(ge=0, le=3)
    now: float


class MoveToModel(Strict):
    tenantId: str = ID
    band: int = Field(ge=0, le=3)
    version: str = Field(min_length=1, max_length=64)
    switchedWeek: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    now: float


class ApproveCandidate(Strict):
    tenantId: str = ID
    band: int = Field(ge=0, le=3)
    version: str = Field(min_length=1, max_length=64)
    approvedBy: str = Field(min_length=1, max_length=90)
    now: float


class Fallback(Strict):
    tenantId: str = ID
    band: int = Field(ge=0, le=3)
    reason: str = Field(max_length=300)
    now: float
    actor: str = Field(max_length=80)


class Tenant(Strict):
    tenantId: str = ID


class PromotionPlan(Strict):
    tenantIds: list[str] = Field(max_length=10_000)
    candidate: str = Field(min_length=1, max_length=64)


class Promote(Strict):
    plan: dict[str, list[dict]]
    candidate: str = Field(min_length=1, max_length=64)
    timezones: dict[str, str]
    operator: str = Field(max_length=80)
    now: float


class Purge(Strict):
    now: float
    retention: dict[str, tuple[int, str]]


def state_of(st) -> dict:
    return {"band": st.band, "state": st.state, "model_version": st.model_version, "streak": st.streak,
            "counted_week": st.counted_week, "evaluated_at": st.evaluated_at, "figures": dict(st.figures or {})}


def _expire_links_hourly(stop) -> None:
    import logging
    import time
    while not stop.wait(3600):
        try:
            with bdb.session() as bs:
                engine.expire_links(bs, time.time())
        except Exception as e:
            logging.getLogger("amfa.bands").error("link expiry failed", extra={"error": type(e).__name__})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import threading
    import time
    logs.setup("bands")
    if config.get().production and config.get().role != "bands":
        raise RuntimeError("the band service runs with AMFA_ROLE=bands")
    bdb.migrate()
    with bdb.session() as bs:                        # links whose time came while the service was down go at once
        engine.expire_links(bs, time.time())
    stop = threading.Event()
    threading.Thread(target=_expire_links_hourly, args=(stop,), daemon=True, name="bands-link-expiry").start()
    yield
    stop.set()


app = FastAPI(title="AMFA band switch", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
web.install(app, body_limits=[("/v1/", 1024 * 1024)])
AUTH = [Depends(internal_auth)]


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/v1/evaluate", dependencies=AUTH)
def evaluate(b: Evaluate):
    ev = Events()
    with bdb.session() as bs:
        states = engine.evaluate(bs, b.tenantId, CollectionPolicy.model_validate(b.collection), b.timezone, b.version, b.candidate,
                                 b.now, ev)
        return {"states": {str(k): state_of(v) for k, v in states.items()}, "events": list(ev)}


@app.post("/v1/due", dependencies=AUTH)
def due(b: TenantNow):
    with bdb.session() as bs:
        return {"due": engine.due(bs, b.tenantId, b.now)}


@app.post("/v1/decision-state", dependencies=AUTH)
def decision_state(b: DecisionState):
    with bdb.session() as bs:
        state, version = engine.decision_state(bs, b.tenantId, b.band, b.now)
        return {"state": state, "version": version}


@app.post("/v1/band", dependencies=AUTH)
def band(b: TenantBand):
    with bdb.session() as bs:
        return {"band": engine.band_snapshot(bs, b.tenantId, b.band)}


@app.post("/v1/candidate", dependencies=AUTH)
def candidate(b: TenantBand):
    with bdb.session() as bs:
        return {"candidate": engine.candidate_snapshot(bs, b.tenantId, b.band)}


@app.post("/v1/move-to-model", dependencies=AUTH)
def move_to_model(b: MoveToModel):
    with bdb.session() as bs:
        return {"error": engine.move_to_model(bs, b.tenantId, b.band, b.version, b.switchedWeek, b.now)}


@app.post("/v1/approve-candidate", dependencies=AUTH)
def approve_candidate(b: ApproveCandidate):
    with bdb.session() as bs:
        return {"error": engine.approve_candidate(bs, b.tenantId, b.band, b.version, b.approvedBy, b.now)}


@app.post("/v1/fallback", dependencies=AUTH)
def fallback(b: Fallback):
    ev = Events()
    with bdb.session() as bs:
        changed = engine.fallback(bs, b.tenantId, b.band, b.reason, b.now, ev, b.actor)
        return {"changed": changed, "events": list(ev)}


@app.post("/v1/reset", dependencies=AUTH)
def reset(b: Tenant):
    with bdb.session() as bs:
        engine.reset_all(bs, b.tenantId)
        return {"ok": True}


@app.post("/v1/promotion-plan", dependencies=AUTH)
def promotion_plan(b: PromotionPlan):
    with bdb.session() as bs:
        return {"plan": engine.promotion_plan(bs, b.tenantIds, b.candidate)}


@app.post("/v1/promote", dependencies=AUTH)
def promote(b: Promote):
    ev = Events()
    with bdb.session() as bs:
        engine.promote(bs, b.plan, b.candidate, b.timezones, b.operator, b.now, ev)
        return {"events": list(ev)}


@app.post("/v1/rows", dependencies=AUTH)
def add_row(b: AddRow):
    ev = Events()
    with bdb.session() as bs:
        row = engine.add_row(bs, b.fields.model_dump(), b.now, ev)
        return {"stored": row is not None, "id": row.id if row is not None else None, "events": list(ev)}


@app.post("/v1/labels", dependencies=AUTH)
def apply_label(b: Label):
    ev = Events()
    with bdb.session() as bs:
        return {"applied": engine.apply_label(bs, b.link, b.label, b.source, b.now, ev), "events": list(ev)}


@app.post("/v1/purge", dependencies=AUTH)
def purge(b: Purge):
    with bdb.session() as bs:
        return engine.purge(bs, b.now, {k: (int(v[0]), str(v[1])) for k, v in b.retention.items()})
