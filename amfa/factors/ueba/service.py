from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, delete, insert, select

from amfa import config
from amfa.common import artifacts, logs, web
from amfa.common.contract import (FAILED, ID_PATTERN, INSUFFICIENT_DATA, PASSED, EnrollResult,
                                  FactorResult, VerifyRequest)
from amfa.factors.base import ReplayGuard, internal_auth, store_for
from amfa.factors.ueba.features import keystroke as ks
from amfa.factors.ueba.features import mouse as ms
from amfa.factors.ueba.features import session as se
from amfa.factors.ueba.features import shape as sh
from amfa.factors.ueba.features import transactional as tx
from amfa.factors.ueba.scoring.combine import (CalibrationSet, attenuate, band, combine,
                                               confidence)

FACTOR = "UEBA"
MATCH_THRESHOLD = 0.5

FULL_CONFIDENCE_EVENTS = 40
FULL_CONFIDENCE_SESSIONS = 20
MIN_SCOREABLE_EVENTS = 3
MIN_SCOREABLE_SESSIONS = 3
CHALLENGE_DIMENSIONS = ("keystroke", "shape")
PREVIEW_DIMENSIONS = ("session", "transaction")

MIN_DWELL_STD_MS = 2.0
REPLAY_MEAN_ABS_MS = 1.5
MAX_EVENTS_PER_SAMPLE = 400
MAX_EVENT_MS = 10 * 60 * 1000

store = store_for("ueba")
guard = ReplayGuard(store, same=lambda a, b: float(np.mean(np.abs(a - b))) < REPLAY_MEAN_ABS_MS)
_state: dict[str, Any] = {}

_md = MetaData()
events_t = Table(
    "ueba_event", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False, index=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("action", String(24), nullable=False),
    Column("amount", Float, nullable=True),
    Column("payee_key", String(64), nullable=True),
    Column("device_key", String(64), nullable=False),
    Column("location_key", String(80), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("occurred_at", Float, nullable=False),
)
sessions_t = Table(
    "ueba_session", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(64), nullable=False, index=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("features", Text, nullable=False),
    Column("at", Float, nullable=False),
)
_md.create_all(store.engine)


PIN_FILE = Path(__file__).resolve().parent / "ARTIFACTS.sha256"


def _load_artifacts() -> None:
    import joblib

    art_dir = config.get().models_dir / "ueba"
    pins = {}
    for line in PIN_FILE.read_text().splitlines():
        if line.strip():
            digest, name = line.split()
            pins[name] = digest
    for name, digest in pins.items():
        if artifacts.sha256_file(art_dir / name) != digest:
            raise artifacts.ArtifactError(f"UEBA artifact {name} failed its integrity check")
    bundle = joblib.load(art_dir / "isoforest.joblib")
    cal = joblib.load(art_dir / "calibrator.joblib")
    _state.update(
        forest=bundle["model"], scaler=bundle["scaler"],
        tx_a=float(cal["a"]), tx_b=float(cal["b"]),
        calset=CalibrationSet.load(art_dir),
        version="isoforest-1.0.0+calibrated",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logs.setup("ueba")
    _load_artifacts()
    yield


app = FastAPI(title="AMFA behavioural factor", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
web.install(app, body_limits=[("/factor/", 4 * 1024 * 1024), ("/risk/", 512 * 1024),
                              ("/events/", 512 * 1024)])


class Reject(Exception):
    def __init__(self, code: str):
        self.code = code


def typed_text(events: list[dict]) -> str:
    out: list[str] = []
    for e in events:
        k = e.get("key", "")
        if k == "Backspace":
            if out:
                out.pop()
        elif isinstance(k, str) and len(k) == 1:
            out.append(k)
    return "".join(out)


def _clean_key_events(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or not (6 <= len(raw) <= MAX_EVENTS_PER_SAMPLE):
        raise Reject("KEYSTROKE_SAMPLE_INVALID")
    out = []
    for e in raw:
        if not isinstance(e, dict):
            raise Reject("KEYSTROKE_SAMPLE_INVALID")
        key, down, up = e.get("key"), e.get("down"), e.get("up")
        if not isinstance(key, str) or len(key) > 16:
            raise Reject("KEYSTROKE_SAMPLE_INVALID")
        if not isinstance(down, (int, float)) or (up is not None and not isinstance(up, (int, float))):
            raise Reject("KEYSTROKE_SAMPLE_INVALID")
        if up is not None and not (0 <= up - down <= 5000):
            raise Reject("KEYSTROKE_TIMING_IMPLAUSIBLE")
        out.append({"key": key, "down": float(down), "up": None if up is None else float(up)})
    if out[-1]["down"] - out[0]["down"] > MAX_EVENT_MS:
        raise Reject("KEYSTROKE_TIMING_IMPLAUSIBLE")
    return out


def _keystroke(raw: Any) -> tuple[dict, list[dict]]:
    events = _clean_key_events(raw)
    if typed_text(events) != ks.ENROLL_PHRASE:
        raise Reject("PHRASE_MISMATCH")
    sample = ks.extract(events)
    if sample is None:
        raise Reject("KEYSTROKE_SAMPLE_TOO_SHORT")
    if float(np.std(sample["dwell"])) < MIN_DWELL_STD_MS:
        raise Reject("KEYSTROKE_AUTOMATION_SUSPECTED")
    return sample, events


def _points(raw: Any, limit: int = 5000) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > limit:
        raise Reject("POINTER_TRACE_INVALID")
    out = []
    for p in raw:
        if not isinstance(p, dict):
            raise Reject("POINTER_TRACE_INVALID")
        x, y, t = p.get("x"), p.get("y"), p.get("t")
        if not all(isinstance(v, (int, float)) for v in (x, y, t)):
            raise Reject("POINTER_TRACE_INVALID")
        out.append({"x": float(x), "y": float(y), "t": float(t)})
    return out


def _timing_vector(sample: dict) -> np.ndarray:
    return np.concatenate([sample["dwell"], sample["flight"]]).astype(np.float32)


def _digest(sample: dict) -> str:
    from hashlib import sha256
    return sha256(np.round(_timing_vector(sample), 1).tobytes()).hexdigest()


@app.get("/enroll/phrase", dependencies=[Depends(internal_auth)])
def enroll_phrase() -> dict:
    return {"phrase": ks.ENROLL_PHRASE, "repetitions": ks.TARGET_SAMPLES,
            "minimum": ks.MIN_SAMPLES + ks.DISCARD_FIRST, "discarded": ks.DISCARD_FIRST,
            "shape": sh.SHAPE, "shapeTracesRequired": sh.MIN_SAMPLES}


class EnrollPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenantId: str = Field(pattern=ID_PATTERN)
    userId: str = Field(pattern=ID_PATTERN)
    payload: dict[str, Any]


@app.post("/factor/enroll", response_model=EnrollResult, dependencies=[Depends(internal_auth)])
def enroll(req: EnrollPayload) -> EnrollResult:
    t0 = time.perf_counter()
    ms_ = lambda: int((time.perf_counter() - t0) * 1000)
    p = req.payload
    raw_samples = p.get("keystrokeSamples")
    if not isinstance(raw_samples, list) or len(raw_samples) > 20:
        return EnrollResult(factor=FACTOR, enrolled=False, reason="KEYSTROKE_SAMPLES_MISSING", latencyMs=ms_())

    usable, rejected = [], {}
    for raw in raw_samples:
        try:
            s, _ = _keystroke(raw)
            usable.append(s)
        except Reject as r:
            rejected[r.code] = rejected.get(r.code, 0) + 1

    digests = [_digest(s) for s in usable]
    if len(set(digests)) != len(digests):
        return EnrollResult(factor=FACTOR, enrolled=False, reason="DUPLICATE_SAMPLES", latencyMs=ms_())

    tpl = ks.build_template(usable)
    if tpl is None:
        return EnrollResult(factor=FACTOR, enrolled=False, reason="NOT_ENOUGH_USABLE_SAMPLES",
                            quality={"usable": len(usable), "rejected": rejected,
                                     "required": ks.MIN_SAMPLES + ks.DISCARD_FIRST},
                            latencyMs=ms_())

    shape_tpl = None
    shape_info: dict = {"status": "NOT_ENROLLED"}
    try:
        traces = [sh.extract(_points(t)) for t in (p.get("shapeTraces") or [])[:10]]
        shape_tpl = sh.build_template(traces)
        shape_info = ({"status": "ENROLLED", "samples": shape_tpl["n_samples"]} if shape_tpl
                      else {"status": "NOT_ENROLLED", "reason": "need 5 complete traces"})
    except Reject as r:
        shape_info = {"status": "NOT_ENROLLED", "reason": r.code}

    mouse_tpl = None
    try:
        segs = [_points(s) for s in (p.get("mouseSegments") or [])[:20]]
        clicks = [c for c in (p.get("mouseClicks") or [])[:500] if isinstance(c, dict)]
        samples = []
        for seg in segs:
            if len(seg) >= ms.MIN_POINTS:
                lo, hi = seg[0]["t"], seg[-1]["t"]
                samples.append(ms.extract(seg, [c for c in clicks
                                                if isinstance(c.get("down"), (int, float))
                                                and lo <= c["down"] <= hi]))
        mouse_tpl = ms.build_template(samples)
    except Reject:
        mouse_tpl = None

    store.put(req.tenantId, req.userId,
              {"keystroke": tpl, "shape": shape_tpl, "mouse": mouse_tpl},
              meta={"keystrokeSamples": tpl["n_samples"], "shape": shape_tpl is not None})
    for s, d in zip(usable, digests):
        guard.remember(req.tenantId, req.userId, d, _timing_vector(s)[: tpl["n_dwell"] + tpl["n_flight"]])
    return EnrollResult(factor=FACTOR, enrolled=True, modelVersion="ueba-keystroke+shape",
                        quality={"keystroke": {"samples": tpl["n_samples"], "rejected": rejected,
                                               "meanDwellMs": round(float(np.mean(tpl["dwell_mean"])), 1)},
                                 "shape": shape_info,
                                 "mouse": "ENROLLED" if mouse_tpl else "NOT_ENROLLED"},
                        latencyMs=ms_())


@app.post("/factor/verify", response_model=FactorResult, dependencies=[Depends(internal_auth)])
def verify(req: VerifyRequest) -> FactorResult:
    t0 = time.perf_counter()
    calset: CalibrationSet = _state["calset"]

    def result(status: str, reason: str | None = None, **kw) -> FactorResult:
        return FactorResult(factor=FACTOR, status=status, failureReason=reason,
                            threshold=MATCH_THRESHOLD, modelVersion="ueba-keystroke+shape",
                            latencyMs=int((time.perf_counter() - t0) * 1000), **kw)

    tpl = store.get(req.tenantId, req.userId)
    if tpl is None or tpl.get("keystroke") is None:
        return result(INSUFFICIENT_DATA, "NO_ENROLLED_TEMPLATE")

    try:
        sample, _ = _keystroke(req.payload.get("keystrokeSample"))
    except Reject as r:
        return result(FAILED, r.code)

    ktpl = tpl["keystroke"]
    vec = _timing_vector(sample)
    vec_cmp = vec[: ktpl["n_dwell"] + ktpl["n_flight"]]
    digest = _digest(sample)
    hit = guard.check(req.tenantId, req.userId, digest, vec_cmp)
    if hit:
        from amfa.common.contract import SPOOF_DETECTED
        return result(SPOOF_DETECTED, hit)
    guard.remember(req.tenantId, req.userId, digest, vec_cmp)

    dims: dict[str, dict] = {}
    probs: dict[str, Optional[float]] = {}
    d = ks.distance(ktpl, sample)
    probs["keystroke"] = calset.probability("keystroke", d)
    dims["keystroke"] = {"status": "OK", "distance": round(d, 4) if d is not None else None,
                         "probability": _r(probs["keystroke"])}

    trace = req.payload.get("shapeTrace")
    if trace is None:
        dims["shape"] = {"status": "NOT_PROVIDED"}
        probs["shape"] = None
    elif tpl.get("shape") is None:
        dims["shape"] = {"status": "INSUFFICIENT_DATA", "reason": "no shape template enrolled"}
        probs["shape"] = None
    else:
        try:
            ss = sh.extract(_points(trace))
        except Reject as r:
            return result(FAILED, r.code)
        sd = sh.distance(tpl["shape"], ss)
        probs["shape"] = calset.probability("shape", sd)
        dims["shape"] = {"status": "OK" if sd is not None else "INCOMPLETE",
                         "distance": _r(sd), "probability": _r(probs["shape"])}

    pts = req.payload.get("mousePoints") or []
    if tpl.get("mouse") and isinstance(pts, list) and len(pts) >= ms.MIN_POINTS:
        try:
            msamp = ms.extract(_points(pts), [c for c in (req.payload.get("mouseClicks") or [])
                                              if isinstance(c, dict)][:500])
            md = ms.distance(tpl["mouse"], msamp)
            dims["mouse"] = {"status": "OK", "distance": _r(md),
                             "probability": _r(calset.probability("mouse", md)),
                             "excludedFromDecision": True}
        except (Reject, Exception):
            dims["mouse"] = {"status": "UNSCORABLE", "excludedFromDecision": True}

    scoring = {k: v for k, v in probs.items() if k in CHALLENGE_DIMENSIONS and v is not None}
    risk, used = combine(scoring, reliability={k: calset.reliability(k) for k in scoring})
    if not used:
        return result(INSUFFICIENT_DATA, "NOTHING_ASSESSABLE", detail={"dimensions": dims})
    score = round(1.0 - risk, 4)
    status = PASSED if score >= MATCH_THRESHOLD else FAILED
    return result(status, None if status == PASSED else "BEHAVIOURAL_MISMATCH", score=score,
                  detail={"dimensions": dims, "dimensionsUsed": used, "band": band(risk)})


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 4)


class Action(str, Enum):
    LOGIN = "LOGIN"
    TRANSFER = "TRANSFER"
    ADD_BENEFICIARY = "ADD_BENEFICIARY"
    CHANGE_LIMIT = "CHANGE_LIMIT"
    PAYMENT = "PAYMENT"


class SessionTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float
    end: float
    actions: list[float] = Field(default_factory=list, max_length=2000)


class TxnFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenantId: str = Field(pattern=ID_PATTERN)
    userId: str = Field(pattern=ID_PATTERN)
    action: Action = Action.TRANSFER
    amount: Optional[float] = Field(default=None, ge=0)
    payeeKey: Optional[str] = Field(default=None, max_length=64)
    deviceKey: str = Field(max_length=64)
    locationKey: str = Field(max_length=80)
    channel: str = Field(default="MOBILE", max_length=16)
    occurredAt: Optional[datetime] = None
    session: Optional[SessionTelemetry] = None


def _history(tenant: str, user: str) -> tuple[list[dict], list[dict]]:
    import json as _json
    with store.engine.connect() as c:
        ev = c.execute(select(events_t).where(events_t.c.tenant_id == tenant,
                                              events_t.c.user_id == user)
                       .order_by(events_t.c.occurred_at).limit(2000)).mappings().all()
        ss = c.execute(select(sessions_t.c.features).where(sessions_t.c.tenant_id == tenant,
                                                           sessions_t.c.user_id == user)
                       .order_by(sessions_t.c.id.desc()).limit(200)).all()
    hist = [{"user_id": user, "amount": e["amount"] or 0.0, "beneficiary_id": e["payee_key"] or "",
             "device_id": e["device_key"], "city": e["location_key"],
             "occurred_at": e["occurred_at"]} for e in ev]
    return hist, [_json.loads(s.features) for s in ss]


@app.post("/risk/preview", dependencies=[Depends(internal_auth)])
def risk_preview(req: TxnFacts) -> dict:
    t0 = time.perf_counter()
    calset: CalibrationSet = _state["calset"]
    events, sessions = _history(req.tenantId, req.userId)
    occurred = (req.occurredAt or datetime.now(timezone.utc)).timestamp()
    dims: dict[str, dict] = {}
    probs: dict[str, Optional[float]] = {}

    feats = se.extract(req.session.model_dump()) if req.session else None
    profile = se.build_profile(sessions)
    if feats is None:
        dims["session"] = {"status": "NOT_PROVIDED"}
        probs["session"] = None
    elif profile is None or len(sessions) < MIN_SCOREABLE_SESSIONS:
        dims["session"] = {"status": INSUFFICIENT_DATA, "sessionsSeen": len(sessions),
                           "sessionsRequired": MIN_SCOREABLE_SESSIONS}
        probs["session"] = None
    else:
        dist = se.distance(profile, feats)
        conf = confidence(len(sessions), FULL_CONFIDENCE_SESSIONS)
        raw = calset.probability("session", dist)
        probs["session"] = attenuate(raw, conf) if raw is not None else None
        dims["session"] = {"status": "OK" if conf >= 1 else "WARMING_UP", "distance": _r(dist),
                           "probability": _r(probs["session"]), "confidence": round(conf, 2)}

    if len(events) < MIN_SCOREABLE_EVENTS:
        dims["transaction"] = {"status": INSUFFICIENT_DATA, "eventsSeen": len(events),
                               "eventsRequired": MIN_SCOREABLE_EVENTS}
        probs["transaction"] = None
    else:
        event = {"user_id": req.userId, "amount": req.amount or 0.0,
                 "beneficiary_id": req.payeeKey or "", "device_id": req.deviceKey,
                 "city": req.locationKey, "occurred_at": occurred}
        fv = tx.compute_one(event, events)
        if fv is None:
            dims["transaction"] = {"status": INSUFFICIENT_DATA}
            probs["transaction"] = None
        else:
            frame = pd.DataFrame([fv.as_dict()])[tx.FEATURE_ORDER]
            score = -_state["forest"].score_samples(_state["scaler"].transform(frame))
            raw = float(1.0 / (1.0 + np.exp(_state["tx_a"] * score[0] + _state["tx_b"])))
            conf = confidence(len(events), FULL_CONFIDENCE_EVENTS)
            probs["transaction"] = attenuate(raw, conf)
            dims["transaction"] = {"status": "OK" if conf >= 1 else "WARMING_UP",
                                   "anomaly": round(float(score[0]), 4),
                                   "probability": _r(probs["transaction"]),
                                   "confidence": round(conf, 2), "eventsSeen": len(events),
                                   "features": {k: round(float(v), 3) for k, v in fv.as_dict().items()}}

    scoring = {k: v for k, v in probs.items() if k in PREVIEW_DIMENSIONS and v is not None}
    risk, used = combine(scoring, reliability={k: calset.reliability(k) for k in scoring})
    if not used:
        b, risk_out, status = "HIGH", None, INSUFFICIENT_DATA
    else:
        b, risk_out, status = band(risk), round(risk, 4), "OK"
    return {"userId": req.userId, "riskScore": risk_out, "band": b, "status": status,
            "dimensions": dims, "dimensionsUsed": used, "modelVersion": _state["version"],
            "eventsSeen": len(events), "sessionsSeen": len(sessions),
            "latencyMs": int((time.perf_counter() - t0) * 1000), "recorded": False}


@app.post("/events/record", dependencies=[Depends(internal_auth)])
def record(req: TxnFacts) -> dict:
    import json as _json
    occurred = (req.occurredAt or datetime.now(timezone.utc)).timestamp()
    with store.engine.begin() as c:
        c.execute(insert(events_t).values(
            tenant_id=req.tenantId, user_id=req.userId, action=req.action.value,
            amount=req.amount, payee_key=req.payeeKey, device_key=req.deviceKey,
            location_key=req.locationKey, channel=req.channel, occurred_at=occurred))
        if req.session:
            f = se.extract(req.session.model_dump())
            if f is not None:
                c.execute(insert(sessions_t).values(tenant_id=req.tenantId, user_id=req.userId,
                                                    features=_json.dumps(f), at=time.time()))
    return {"recorded": True}


@app.delete("/factor/templates/{tenant_id}/{user_id}", dependencies=[Depends(internal_auth)])
def erase(tenant_id: str, user_id: str) -> dict:
    deleted = store.delete(tenant_id, user_id)
    with store.engine.begin() as c:
        c.execute(delete(events_t).where(events_t.c.tenant_id == tenant_id, events_t.c.user_id == user_id))
        c.execute(delete(sessions_t).where(sessions_t.c.tenant_id == tenant_id,
                                           sessions_t.c.user_id == user_id))
    return {"factor": FACTOR, "deleted": deleted}


@app.get("/health")
def health() -> dict:
    return {"service": "ueba", "status": "up" if _state.get("forest") is not None else "degraded",
            "model": _state.get("version"), "calibrated": sorted(_state["calset"].calibrators)
            if _state.get("calset") else [], "store": store.ping()}
