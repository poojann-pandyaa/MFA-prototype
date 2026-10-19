from __future__ import annotations

import os
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
from amfa.factors.ueba import dimensions as dm
from amfa.factors.ueba.features import keystroke as ks
from amfa.factors.ueba.features import mouse as ms
from amfa.factors.ueba.features import session as se
from amfa.factors.ueba.features import shape as sh
from amfa.factors.ueba.features import transactional as tx
from amfa.factors.ueba.scoring.combine import (COMPLETE_COVERAGE, CalibrationSet, attenuate, band,
                                               combine, confidence)

FACTOR = "UEBA"
MATCH_THRESHOLD = 0.5

FULL_CONFIDENCE_EVENTS = 40
FULL_CONFIDENCE_SESSIONS = 20
MIN_SCOREABLE_EVENTS = 3
MIN_SCOREABLE_SESSIONS = 3
CHALLENGE_DIMENSIONS = ("keystroke", "shape")
PREVIEW_DIMENSIONS = ("session", "transaction")

# Touch scoring: "live" decides with whatever calibration exists (provisional until real touch
# measurements are fitted); "shadow" scores and logs but answers INSUFFICIENT_DATA so the
# engine substitutes another factor.
TOUCH_MODE = os.getenv("AMFA_UEBA_TOUCH_MODE", "live")

MIN_DWELL_STD_MS = 2.0
MIN_AUTOMATION_CHECK_KEYS = 5
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


def _clean_key_events(raw: Any, *, min_events: int = 0) -> list[dict]:
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not (min_events <= len(raw) <= MAX_EVENTS_PER_SAMPLE):
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
    if out and out[-1]["down"] - out[0]["down"] > MAX_EVENT_MS:
        raise Reject("KEYSTROKE_TIMING_IMPLAUSIBLE")
    return out


def _automated(sample: dict, input: str = "keyboard") -> bool:
    """Timing too regular for a person. On a keyboard that is the hold times. A phone keyboard
    reports every hold as about 3 ms whoever is typing, so there the gaps between presses are
    checked instead; otherwise every phone sample would be flagged."""
    field = "dwell" if input == "keyboard" else "interval"
    x = sample[field][~np.isnan(sample[field])]
    return len(x) >= MIN_AUTOMATION_CHECK_KEYS and float(np.std(x)) < MIN_DWELL_STD_MS


def _points(raw: Any, limit: int = 5000) -> list[dict]:
    if raw is None:
        return []
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


def _templates(tpl: Optional[dict], input: str) -> tuple[Optional[dict], Optional[dict]]:
    """(keystroke, shape) templates for one kind of input. The keyboard set sits at the top level,
    where templates made before touch support already are; the touch set under "touch"."""
    if not tpl:
        return None, None
    if input == "touch":
        t = tpl.get("touch") or {}
        return t.get("keystroke"), t.get("shape")
    return tpl.get("keystroke"), tpl.get("shape")


def _inputs(tpl: Optional[dict]) -> dict[str, dict[str, bool]]:
    return {i: {"keystroke": k is not None, "shape": bool(sp) and "split_mean" in sp}
            for i in ks.INPUTS for k, sp in [_templates(tpl, i)]}


def _enrolled(tpl: Optional[dict]) -> list[str]:
    return [i for i, v in _inputs(tpl).items() if v["keystroke"]]


def _timing_vector(sample: dict, input: str = "keyboard") -> np.ndarray:
    if input == "touch":
        return np.nan_to_num(sample["interval"]).astype(np.float32)
    return np.nan_to_num(np.concatenate([sample["dwell"], sample["flight"]])).astype(np.float32)


def _digest(sample: dict, input: str = "keyboard") -> str:
    from hashlib import sha256
    return sha256(np.round(_timing_vector(sample, input), 1).tobytes()).hexdigest()


@app.get("/enroll/phrase", dependencies=[Depends(internal_auth)])
def enroll_phrase() -> dict:
    return {"phrase": ks.ENROLL_PHRASE, "repetitions": ks.TARGET_SAMPLES,
            "minimum": ks.MIN_SAMPLES + ks.DISCARD_FIRST, "discarded": ks.DISCARD_FIRST,
            "shape": [list(p) for p in sh.SHAPE], "aspectRatio": sh.ASPECT,
            "shapeTracesRequired": sh.MIN_SAMPLES, "inputs": list(ks.INPUTS)}


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
    # One template set per kind of input; enrolling one keeps the other.
    input = p.get("input") or "keyboard"
    if input not in ks.INPUTS:
        return EnrollResult(factor=FACTOR, enrolled=False, reason="INPUT_INVALID", latencyMs=ms_())
    raw_traces = p.get("shapeTraces") or []
    kinds = p.get("shapePointerTypes") or []
    if not isinstance(kinds, list) or (kinds and len(kinds) != len(raw_traces)) or \
            (input == "touch" and raw_traces and len(kinds) != len(raw_traces)):
        return EnrollResult(factor=FACTOR, enrolled=False, reason="SHAPE_POINTER_TYPES_INVALID", latencyMs=ms_())
    if any(sh.POINTER_INPUT.get(k if isinstance(k, str) else "") != input for k in kinds):
        return EnrollResult(factor=FACTOR, enrolled=False, reason="SHAPE_POINTER_MISMATCH", latencyMs=ms_())

    usable, rejected = [], {}

    def reject(code: str) -> None:
        rejected[code] = rejected.get(code, 0) + 1

    for raw in raw_samples:
        try:
            sample = ks.extract(_clean_key_events(raw, min_events=1))
        except Reject as r:
            reject(r.code)
            continue
        if sample["coverage"] < ks.MIN_ENROLL_COVERAGE:
            reject("PHRASE_INCOMPLETE")
        elif _automated(sample, input):
            reject("KEYSTROKE_AUTOMATION_SUSPECTED")
        else:
            usable.append(sample)

    digests = [_digest(s, input) for s in usable]
    if len(set(digests)) != len(digests):
        return EnrollResult(factor=FACTOR, enrolled=False, reason="DUPLICATE_SAMPLES", latencyMs=ms_())

    tpl = ks.build_template(usable, input=input)
    if tpl is None:
        return EnrollResult(factor=FACTOR, enrolled=False, reason="NOT_ENOUGH_USABLE_SAMPLES",
                            quality={"usable": len(usable), "rejected": rejected,
                                     "required": ks.MIN_SAMPLES},
                            latencyMs=ms_())

    shape_tpl = None
    shape_info: dict = {"status": "NOT_ENROLLED"}
    try:
        traces = [sh.extract(_points(t)) for t in raw_traces[:10]]
        shape_tpl = sh.build_template(traces, input=input)
        shape_info = ({"status": "ENROLLED", "samples": shape_tpl["n_samples"]} if shape_tpl
                      else {"status": "NOT_ENROLLED", "reason": f"need {sh.MIN_SAMPLES} complete traces"})
    except Reject as r:
        shape_info = {"status": "NOT_ENROLLED", "reason": r.code}

    mouse_tpl = None
    try:
        if input != "keyboard":       # free mouse movement is a keyboard-and-mouse signal only
            raise Reject("NOT_KEYBOARD")
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

    record = dict(store.get(req.tenantId, req.userId) or {})
    if input == "keyboard":
        record.update(keystroke=tpl, shape=shape_tpl, mouse=mouse_tpl)
    else:
        record[input] = {"keystroke": tpl, "shape": shape_tpl}
    store.put(req.tenantId, req.userId, record,
              meta={"keystrokeSamples": tpl["n_samples"], "shape": shape_tpl is not None,
                    "inputs": _enrolled(record)})
    for s, d in zip(usable, digests):
        guard.remember(req.tenantId, req.userId, d, _timing_vector(s, input)[: _vec_len(tpl, input)])
    return EnrollResult(factor=FACTOR, enrolled=True, modelVersion="ueba-keystroke+shape",
                        quality={"input": input, "enrolledInputs": _enrolled(record),
                                 "inputs": _inputs(record),
                                 "keystroke": {"samples": tpl["n_samples"], "rejected": rejected,
                                               "compares": tpl["fields"],
                                               "meanDwellMs": round(float(np.nanmean(
                                                   [v for v in tpl["dwell_mean"] if v is not None])), 1)},
                                 "shape": shape_info,
                                 "mouse": "ENROLLED" if mouse_tpl else "NOT_ENROLLED"},
                        latencyMs=ms_())


def _vec_len(ktpl: dict, input: str) -> int:
    return ktpl["n_flight"] if input == "touch" else ktpl["n_dwell"] + ktpl["n_flight"]


@app.post("/factor/verify", response_model=FactorResult, dependencies=[Depends(internal_auth)])
def verify(req: VerifyRequest) -> FactorResult:
    t0 = time.perf_counter()
    calset: CalibrationSet = _state["calset"]

    def result(status: str, reason: str | None = None, **kw) -> FactorResult:
        return FactorResult(factor=FACTOR, status=status, failureReason=reason,
                            threshold=MATCH_THRESHOLD, modelVersion="ueba-keystroke+shape",
                            latencyMs=int((time.perf_counter() - t0) * 1000), **kw)

    tpl = store.get(req.tenantId, req.userId)
    # The challenge says which template set this attempt is answered against.
    input = str(req.challenge.get("input") or "keyboard")
    if input not in ks.INPUTS:
        return result(FAILED, "CHALLENGE_INPUT_INVALID")
    ktpl, stpl = _templates(tpl, input)
    if ktpl is None:
        # Nothing to compare for this kind of input: never a pass; the engine substitutes.
        return result(INSUFFICIENT_DATA, "NO_ENROLLED_TEMPLATE",
                      detail={"input": input, "enrolledInputs": _enrolled(tpl)})

    try:
        key_events = _clean_key_events(req.payload.get("keystrokeSample"))
        shape_asked = bool(req.challenge.get("shape"))
        trace = _points(req.payload.get("shapeTrace")) if shape_asked else []
    except Reject as r:
        return result(FAILED, r.code)

    # Each part must be entered with the challenge's input: typing on a keyboard against the touch
    # set (or a finger trace against the mouse set) fails rather than borrowing the other set.
    typed_with = req.payload.get("keystrokeInput") or "keyboard"
    pointer = req.payload.get("shapePointerType")
    drawn_with = sh.POINTER_INPUT.get(pointer) if isinstance(pointer, str) else "keyboard"
    if (key_events and typed_with != input) or (trace and drawn_with != input):
        return result(FAILED, "INPUT_MISMATCH",
                      detail={"input": input, "keystrokeInput": typed_with,
                              "shapePointerType": pointer if isinstance(pointer, str) else None})

    sample = ks.extract(key_events)
    if _automated(sample, input):
        return result(FAILED, "KEYSTROKE_AUTOMATION_SUSPECTED")
    if sample["coverage"] >= COMPLETE_COVERAGE:
        vec = _timing_vector(sample, input)[: _vec_len(ktpl, input)]
        digest = _digest(sample, input)
        hit = guard.check(req.tenantId, req.userId, digest, vec)
        if hit:
            from amfa.common.contract import SPOOF_DETECTED
            return result(SPOOF_DETECTED, hit)
        guard.remember(req.tenantId, req.userId, digest, vec)

    dims: dict[str, dict] = {}
    probs: dict[str, Optional[float]] = {}
    dims["keystroke"], probs["keystroke"] = dm.score_keystroke(ktpl, key_events, calset, input=input)
    dims["shape"], probs["shape"] = dm.score_shape(stpl, trace, calset, input=input,
                                                   applicable=shape_asked)

    pts = req.payload.get("mousePoints") or []
    if input == "keyboard" and tpl.get("mouse") and isinstance(pts, list) and len(pts) >= ms.MIN_POINTS:
        try:
            clicks = [c for c in (req.payload.get("mouseClicks") or []) if isinstance(c, dict)][:500]
            dims["mouse"], _ = dm.score_mouse(tpl["mouse"], _points(pts), clicks, calset)
            dims["mouse"]["excludedFromDecision"] = True
        except (Reject, Exception):
            dims["mouse"] = {"status": "UNSCORABLE", "excludedFromDecision": True}

    scoring = {k: v for k, v in probs.items() if k in CHALLENGE_DIMENSIONS and v is not None}
    # Weight each part by the reliability of the calibrator that actually scored it.
    risk, used = combine(scoring, reliability={k: calset.reliability(dims[k].get("calibration", k))
                                               for k in scoring})
    if not used:
        return result(INSUFFICIENT_DATA, "NOTHING_ASSESSABLE", detail={"dimensions": dims, "input": input})
    score = round(1.0 - risk, 4)
    status, reason = _verdict(dims, score)
    detail = {"dimensions": dims, "dimensionsUsed": used, "band": band(risk), "input": input,
              "provisional": any(dims[k].get("provisional") for k in used)}
    if input == "touch" and TOUCH_MODE == "shadow":
        # Scored and logged for measurement, not trusted for the decision.
        return result(INSUFFICIENT_DATA, "TOUCH_NOT_VALIDATED", detail={**detail, "shadowScore": score})
    return result(status, reason, score=score, detail=detail)


def _verdict(dims: dict[str, dict], score: float) -> tuple[str, Optional[str]]:
    """Skipping a whole part of the challenge cannot be passed however well the other part went,
    otherwise that part would be optional in practice."""
    statuses = [dims[d]["status"] for d in CHALLENGE_DIMENSIONS
                if d in dims and dims[d]["status"] not in ("NOT_APPLICABLE", "INSUFFICIENT_DATA")]
    if statuses and all(st == "MISSING" for st in statuses):
        return FAILED, "CHALLENGE_MISSING"
    if "MISSING" in statuses:
        return FAILED, "CHALLENGE_INCOMPLETE"
    if score >= MATCH_THRESHOLD:
        return PASSED, None
    return FAILED, "CHALLENGE_INCOMPLETE" if "PARTIAL" in statuses else "BEHAVIOURAL_MISMATCH"


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
