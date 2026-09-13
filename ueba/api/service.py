"""
UEBA service: enrollment and verification.

Four behavioural dimensions:

  keystroke   fixed-phrase typing, compared to an enrolled template
  mouse       passive pointer trace during form entry
  session     accumulated session statistics (no enrollment -- longitudinal)
  transaction amount, timing, device, location, beneficiary deviation

Each dimension produces a distance or score on its own scale, is
calibrated independently to a probability, and the probabilities are
combined in log-odds space. No weights are chosen by hand.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from features import keystroke as ks
from features import mouse as ms
from features import session as se
from features import shape as sh
from features import transactional as tx
from models.base import ModelContext
from models.calibrate import Calibrator
from models.loader import load_model
from scoring.combine import (CalibrationSet, attenuate, band, combine,
                             confidence)
from store import db

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "artifacts"))
IP_SALT = os.getenv("IP_SALT", "dev-salt-replace-in-production")
DB_PATH = Path(os.getenv("DB_PATH", "ueba.db"))
STATIC = Path(__file__).parent.parent / "static"

# Observations needed before a dimension is trusted at full strength.
# The minimum thresholds (tx.MIN_EVENTS, se.MIN_SESSIONS) say when a
# feature CAN be computed; these say when the baseline behind it is
# trustworthy. Conflating the two produces a cliff: silence, then a
# confident wrong answer one event later.
FULL_CONFIDENCE_EVENTS = 40
FULL_CONFIDENCE_SESSIONS = 20

# Below this the score is judged too unstable to report at all, and the
# dimension declares INSUFFICIENT_DATA rather than emitting a number with
# near-zero confidence. Set just above the mathematical floor: learning
# still begins at the first event, and every event contributes to the
# profile whether or not it produced a score.
MIN_SCOREABLE_EVENTS = 3
MIN_SCOREABLE_SESSIONS = 3

# Dimensions that contribute to the decision. Every dimension is always
# computed and reported; this controls only which ones the combination
# consumes.
#
# Mouse is excluded by default. Its calibration was fitted on simulated
# pointer traces and does not transfer: on simulated data it measured
# EER 0.098, but in live two-person testing it flagged most genuine
# attempts as impostor. The simulator produces consistent motor profiles,
# whereas real pointer movement varies substantially between sittings --
# posture, trackpad versus mouse, screen size, how rushed the user is.
#
# Reporting it while excluding it from the decision keeps the measurement
# visible without letting an uncalibrated signal drive outcomes.
# Re-enable once the calibration is refitted on real collected traces:
#     DECISION_DIMENSIONS=keystroke,mouse,session,transaction
DECISION_DIMENSIONS = set(
    os.getenv("DECISION_DIMENSIONS",
              "keystroke,shape,session,transaction").split(",")
)

_state: dict[str, Any] = {}


# --------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------- #


class KeyEvent(BaseModel):
    key: str
    down: float
    up: float | None = None


class MousePoint(BaseModel):
    x: float
    y: float
    t: float


class MouseClick(BaseModel):
    down: float
    up: float | None = None


class EnrollRequest(BaseModel):
    """
    Enrollment needs several typing samples, not one.

    A single sample captures one mood. Variance across repetitions is what
    makes verification possible at all: without it there is no way to tell
    a tired legitimate user from an impostor.
    """

    userId: str
    keystrokeSamples: list[list[KeyEvent]] = Field(..., min_length=1)
    # One pointer segment per typing round. Separate rounds are separate
    # interactions with pauses between them, so the variance across them
    # is real between-session variance. Splitting a single continuous
    # trace into chunks measures only within-session variance, which is
    # far tighter and makes ordinary later movement look anomalous.
    mouseSegments: list[list[MousePoint]] = []
    mousePoints: list[MousePoint] = []      # fallback, single trace
    mouseClicks: list[MouseClick] = []
    # One traced attempt at the fixed shape per enrollment round.
    shapeTraces: list[list[MousePoint]] = []


class Action(str, Enum):
    LOGIN = "LOGIN"
    TRANSFER = "TRANSFER"
    ADD_BENEFICIARY = "ADD_BENEFICIARY"
    CHANGE_LIMIT = "CHANGE_LIMIT"


class Channel(str, Enum):
    WEB = "WEB"
    MOBILE = "MOBILE"


class SessionPayload(BaseModel):
    start: float
    end: float
    actions: list[float] = []


class VerifyRequest(BaseModel):
    """
    No free-text fields anywhere. Beneficiary names and remarks are
    attacker-controlled, are needed by no feature, and would open an
    injection path into the decision layer.
    """

    userId: str
    # transaction context
    amount: float | None = None
    deviceId: str
    ip: str
    timestamp: datetime | None = None
    beneficiaryId: str | None = None
    action: Action = Action.TRANSFER
    channel: Channel = Channel.WEB
    # behavioural evidence
    keystrokeSample: list[KeyEvent] | None = None
    mousePoints: list[MousePoint] = []
    mouseClicks: list[MouseClick] = []
    shapeTrace: list[MousePoint] | None = None
    session: SessionPayload | None = None


class DimensionResult(BaseModel):
    status: str            # OK | INSUFFICIENT_DATA | NOT_PROVIDED
    distance: float | None = None
    probability: float | None = None
    detail: dict | None = None


class VerifyResponse(BaseModel):
    userId: str
    riskScore: float | None
    band: str
    decision: str
    dimensions: dict[str, DimensionResult]
    dimensionsUsed: list[str]
    modelVersion: str
    assessedAt: datetime
    latencyMs: int


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def hash_ip(ip: str) -> str:
    """Salted hash: supports "same network as before" without keeping the IP."""
    return hashlib.sha256((IP_SALT + ip).encode()).hexdigest()[:32]


def derive_city(ip: str) -> str:
    """Placeholder for GeoIP. The address is discarded after this call."""
    octet = ip.split(".")[0] if "." in ip else "0"
    cities = ["Bangalore", "Mumbai", "Delhi", "Chennai", "Hyderabad"]
    try:
        return cities[int(octet) % len(cities)]
    except ValueError:
        return "UNKNOWN"


def _events_to_dicts(events) -> list[dict]:
    return [{"key": e.key, "down": e.down, "up": e.up} for e in events]


# --------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------- #

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(DB_PATH)
    try:
        model = load_model("models/isoforest")
        _state.update(
            model=model,
            tx_calibrator=Calibrator.load(ARTIFACTS),
            calset=CalibrationSet.load(ARTIFACTS),
            version=f"{model.name}-{model.version}",
            ready=True,
        )
    except Exception as exc:  # noqa: BLE001
        _state.update(ready=False, error=str(exc),
                      calset=CalibrationSet.load(ARTIFACTS),
                      version="unloaded")
    yield


app = FastAPI(title="UEBA Service", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------- #
# Enrollment
# --------------------------------------------------------------------- #


@app.get("/enroll/phrase")
def enroll_phrase() -> dict:
    """
    The fixed phrase, and how many repetitions are needed.

    Fixed text rather than free text: comparing the same phrase at
    enrollment and verification is a like-for-like comparison. Free text
    means matching different key pairs against each other, which needs far
    more data to be reliable.
    """
    return {
        "phrase": ks.ENROLL_PHRASE,
        "repetitions": ks.TARGET_SAMPLES,
        "minimum": ks.MIN_SAMPLES,
        "discarded": ks.DISCARD_FIRST,
        "mousePointsRequired": ms.MIN_POINTS * ms.MIN_SAMPLES,
        "shape": sh.SHAPE,
        "shapeTracesRequired": sh.MIN_SAMPLES,
        "note": (
            f"The first {ks.DISCARD_FIRST} repetitions are discarded. A "
            "first-time user typing an unfamiliar phrase is hesitant, and "
            "a template built from that learning phase does not represent "
            "how they type once familiar."
        ),
    }


@app.post("/enroll")
def enroll(req: EnrollRequest) -> dict:
    samples = [ks.extract(_events_to_dicts(s)) for s in req.keystrokeSamples]
    usable = [s for s in samples if s is not None]

    if len(usable) < ks.MIN_SAMPLES:
        raise HTTPException(
            400,
            f"need at least {ks.MIN_SAMPLES} usable typing samples, "
            f"got {len(usable)} of {len(req.keystrokeSamples)} submitted",
        )

    keystroke_tpl = ks.build_template(usable)
    if keystroke_tpl is None:
        raise HTTPException(400, "could not build a keystroke template")

    # Mouse enrollment is passive: the trace collected while the user
    # filled this form. Split into segments so a template with variance can
    # be built from a single sitting.
    mouse_tpl = None
    points_needed = ms.MIN_POINTS * ms.MIN_SAMPLES
    clicks = [c.model_dump() for c in req.mouseClicks]

    if req.mouseSegments:
        # Preferred path: one segment per typing round.
        #
        # Clicks are matched to their segment by timestamp. Passing the
        # whole click list to every segment would make click_interval span
        # the gaps between rounds rather than the intervals within one --
        # a value with no counterpart at verification, where only one
        # round's clicks exist. Two of the four compared mouse features
        # are click-based, so this mismatch alone was enough to score a
        # genuine user at 0.9998.
        total_points = sum(len(seg) for seg in req.mouseSegments)
        samples = []
        for seg in req.mouseSegments:
            if len(seg) < ms.MIN_POINTS:
                continue
            pts = [p.model_dump() for p in seg]
            lo, hi = pts[0]["t"], pts[-1]["t"]
            in_seg = [c for c in clicks if lo <= c["down"] <= hi]
            samples.append(ms.extract(pts, in_seg))
        mouse_tpl = ms.build_template(samples)
    else:
        # Fallback for clients that send one continuous trace. Segmenting
        # it understates variance -- recorded in the response so the
        # limitation is visible rather than silent.
        total_points = len(req.mousePoints)
        if total_points >= points_needed:
            pts = [p.model_dump() for p in req.mousePoints]
            chunk = len(pts) // ms.MIN_SAMPLES
            samples = []
            for i in range(ms.MIN_SAMPLES):
                part = pts[i * chunk:(i + 1) * chunk]
                if not part:
                    continue
                lo, hi = part[0]["t"], part[-1]["t"]
                samples.append(ms.extract(
                    part, [c for c in clicks if lo <= c["down"] <= hi]))
            mouse_tpl = ms.build_template(samples)

    shape_tpl = None
    if req.shapeTraces:
        shape_samples = [
            sh.extract([p.model_dump() for p in tr]) for tr in req.shapeTraces
        ]
        shape_tpl = sh.build_template(shape_samples)

    db.save_user(req.userId, keystroke_tpl, mouse_tpl, DB_PATH,
                 shape_tpl=shape_tpl)

    return {
        "userId": req.userId,
        "enrolled": True,
        "keystroke": {
            "samples": keystroke_tpl["n_samples"],
            "positions": keystroke_tpl["n_dwell"],
            "meanDwellMs": round(np.mean(keystroke_tpl["dwell_mean"]), 1),
            "typingSpeedCps": round(keystroke_tpl["speed_mean"], 2),
        },
        "mouse": (
            {"status": "ENROLLED", "samples": mouse_tpl["n_samples"],
             "pointsCaptured": total_points,
             "segmented": bool(req.mouseSegments),
             "note": None if req.mouseSegments else
                     "built from a single continuous trace, which "
                     "understates between-session variance"}
            if mouse_tpl
            else {"status": "NOT_ENROLLED",
                  "pointsCaptured": total_points,
                  "pointsRequired": points_needed,
                  "reason": "insufficient pointer movement captured -- "
                            "move the pointer while typing, and click "
                            "buttons rather than pressing Enter"}
        ),
        "shape": (
            {"status": "ENROLLED", "samples": shape_tpl["n_samples"],
             "checkpoints": shape_tpl["n_checkpoints"]}
            if shape_tpl
            else {"status": "NOT_ENROLLED",
                  "tracesReceived": len(req.shapeTraces),
                  "tracesRequired": sh.MIN_SAMPLES,
                  "reason": "trace the shape from start to end on each "
                            "round; incomplete traces are discarded"}
        ),
        "session": {
            "status": "ACCUMULATES",
            "reason": "session behaviour is longitudinal and cannot be "
                      "captured in one sitting",
            "sessionsRequired": se.MIN_SESSIONS,
        },
    }


# --------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------- #


@app.post("/verify", response_model=VerifyResponse)
def verify(req: VerifyRequest) -> VerifyResponse:
    started = time.perf_counter()

    user = db.get_user(req.userId, DB_PATH)
    if user is None:
        raise HTTPException(404, f"user {req.userId} is not enrolled")

    calset: CalibrationSet = _state.get("calset") or CalibrationSet()
    dims: dict[str, DimensionResult] = {}
    probs: dict[str, float | None] = {}

    # ---- keystroke -------------------------------------------------
    if req.keystrokeSample is None:
        dims["keystroke"] = DimensionResult(status="NOT_PROVIDED")
        probs["keystroke"] = None
    else:
        sample = ks.extract(_events_to_dicts(req.keystrokeSample))
        d = ks.distance(user["keystroke_tpl"], sample)
        p = calset.probability("keystroke", d)
        dims["keystroke"] = DimensionResult(
            status="OK" if d is not None else "INSUFFICIENT_DATA",
            distance=round(d, 4) if d is not None else None,
            probability=round(p, 4) if p is not None else None,
            detail=(
                {k: round(v, 2) for k, v in sample["summary"].items()}
                if sample else None
            ),
        )
        probs["keystroke"] = p

    # ---- mouse -----------------------------------------------------
    if len(req.mousePoints) < ms.MIN_POINTS:
        dims["mouse"] = DimensionResult(
            status="NOT_PROVIDED",
            detail={"pointsCaptured": len(req.mousePoints),
                    "pointsRequired": ms.MIN_POINTS,
                    "reason": "insufficient pointer movement during this "
                              "transaction"},
        )
        probs["mouse"] = None
    elif user["mouse_tpl"] is None:
        dims["mouse"] = DimensionResult(
            status="INSUFFICIENT_DATA",
            detail={"reason": "no mouse template enrolled for this user"},
        )
        probs["mouse"] = None
    else:
        sample = ms.extract([p.model_dump() for p in req.mousePoints],
                            [c.model_dump() for c in req.mouseClicks])
        d = ms.distance(user["mouse_tpl"], sample)
        p = calset.probability("mouse", d)
        dims["mouse"] = DimensionResult(
            status="OK" if d is not None else "INSUFFICIENT_DATA",
            distance=round(d, 4) if d is not None else None,
            probability=round(p, 4) if p is not None else None,
            detail=({k: round(v, 3) for k, v in sample.items()} if sample else None),
        )
        probs["mouse"] = p

    # ---- shape trace -----------------------------------------------
    if req.shapeTrace is None:
        dims["shape"] = DimensionResult(status="NOT_PROVIDED")
        probs["shape"] = None
    elif user.get("shape_tpl") is None:
        dims["shape"] = DimensionResult(
            status="INSUFFICIENT_DATA",
            detail={"reason": "no shape template enrolled for this user"})
        probs["shape"] = None
    else:
        sample = sh.extract([p.model_dump() for p in req.shapeTrace])
        d = sh.distance(user["shape_tpl"], sample)
        p_ = calset.probability("shape", d)
        dims["shape"] = DimensionResult(
            status="OK" if d is not None else "INSUFFICIENT_DATA",
            distance=round(d, 4) if d is not None else None,
            probability=round(p_, 4) if p_ is not None else None,
            detail=({k: round(v, 4) for k, v in sample["summary"].items()}
                    if sample else
                    {"reason": "trace incomplete or too short"}),
        )
        probs["shape"] = p_

    # ---- session ---------------------------------------------------
    # Not an enrollment dimension. The profile is built from accumulated
    # history, so a new user has nothing to compare against.
    session_feats = se.extract(req.session.model_dump()) if req.session else None
    history = db.get_sessions(req.userId, DB_PATH)
    profile = se.build_profile(history)

    if session_feats is None:
        dims["session"] = DimensionResult(status="NOT_PROVIDED")
        probs["session"] = None
    elif profile is None or len(history) < MIN_SCOREABLE_SESSIONS:
        dims["session"] = DimensionResult(
            status="INSUFFICIENT_DATA",
            detail={"sessionsSeen": len(history),
                    "sessionsRequired": MIN_SCOREABLE_SESSIONS,
                    "note": "the profile is being built from this session "
                            "onward"},
        )
        probs["session"] = None
    else:
        d = se.distance(profile, session_feats)
        raw = calset.probability("session", d)
        conf = confidence(len(history), FULL_CONFIDENCE_SESSIONS)
        p = attenuate(raw, conf) if raw is not None else None
        dims["session"] = DimensionResult(
            status="OK" if conf >= 1.0 else "WARMING_UP",
            distance=round(d, 4) if d is not None else None,
            probability=round(p, 4) if p is not None else None,
            detail={
                "sessionsSeen": len(history),
                "confidence": round(conf, 2),
                "rawProbability": round(raw, 4) if raw is not None else None,
                **{k: round(v, 2) for k, v in session_feats.items()},
            },
        )
        probs["session"] = p

    if session_feats is not None:
        db.add_session(req.userId, session_feats, DB_PATH)

    # ---- transaction -----------------------------------------------
    occurred = (req.timestamp or datetime.now(timezone.utc)).timestamp()
    events = db.get_events(req.userId, DB_PATH)

    if len(events) < MIN_SCOREABLE_EVENTS:
        dims["transaction"] = DimensionResult(
            status="INSUFFICIENT_DATA",
            detail={
                "eventsSeen": len(events),
                "eventsRequired": MIN_SCOREABLE_EVENTS,
                "note": "the profile is being built from this event onward; "
                        "a deviation cannot be computed until a standard "
                        "deviation exists",
            },
        )
        probs["transaction"] = None
    elif not _state.get("ready"):
        dims["transaction"] = DimensionResult(
            status="INSUFFICIENT_DATA",
            detail={"reason": "transactional model not loaded"},
        )
        probs["transaction"] = None
    else:
        event = {
            "user_id": req.userId,
            "amount": req.amount or 0.0,
            "beneficiary_id": req.beneficiaryId or "",
            "device_id": req.deviceId,
            "city": derive_city(req.ip),
            "occurred_at": occurred,
        }
        hist = [
            {"user_id": e["user_id"], "amount": e["amount"] or 0.0,
             "beneficiary_id": e["beneficiary_id"] or "",
             "device_id": e["device_id"], "city": e["city"],
             "occurred_at": e["occurred_at"]}
            for e in events
        ]
        fv = tx.compute_one(event, hist)
        if fv is None:
            dims["transaction"] = DimensionResult(status="INSUFFICIENT_DATA")
            probs["transaction"] = None
        else:
            frame = pd.DataFrame([fv.as_dict()])
            model = _state["model"]
            ctx = ModelContext(data=frame, artifact_dir=ARTIFACTS)
            model.validate_features(ctx)
            score = model.infer(ctx)
            raw = float(_state["tx_calibrator"].transform(score)[0])
            conf = confidence(len(events), FULL_CONFIDENCE_EVENTS)
            p = attenuate(raw, conf)
            dims["transaction"] = DimensionResult(
                status="OK" if conf >= 1.0 else "WARMING_UP",
                distance=round(float(score[0]), 4),
                probability=round(p, 4),
                detail={
                    "eventsSeen": len(events),
                    "confidence": round(conf, 2),
                    "rawProbability": round(raw, 4),
                    **{k: round(float(v), 3) for k, v in fv.as_dict().items()},
                },
            )
            probs["transaction"] = p

    # Record the event regardless, so history accumulates.
    db.add_event(req.userId, req.action.value, req.amount, req.beneficiaryId,
                 req.deviceId, hash_ip(req.ip), derive_city(req.ip),
                 req.channel.value, occurred, DB_PATH)

    # ---- combine ---------------------------------------------------
    # Dimensions outside DECISION_DIMENSIONS are still reported above;
    # they are withheld from the combination only.
    scoring = {d: p for d, p in probs.items() if d in DECISION_DIMENSIONS}
    reliability = {d: calset.reliability(d) for d in scoring}
    risk, used = combine(scoring, reliability=reliability)

    for d, result in dims.items():
        if d not in DECISION_DIMENSIONS and result.status in ("OK", "WARMING_UP"):
            result.detail = {**(result.detail or {}),
                             "excludedFromDecision": True,
                             "reason": "calibration not yet fitted on real "
                                       "collected data"}

    if not used:
        # Nothing could be assessed. Absence of evidence is not evidence of
        # safety: escalate rather than allowing.
        decision = "STEP_UP"
        risk_out = None
        risk_band = "HIGH"
    else:
        risk_band = band(risk)
        risk_out = round(risk, 4)
        decision = {"LOW": "ALLOW", "MEDIUM": "STEP_UP", "HIGH": "DENY"}[risk_band]

    latency = int((time.perf_counter() - started) * 1000)
    db.add_verification(req.userId, risk_out, risk_band, decision,
                        {k: v.model_dump() for k, v in dims.items()},
                        _state.get("version", "unloaded"), latency, DB_PATH)

    return VerifyResponse(
        userId=req.userId,
        riskScore=risk_out,
        band=risk_band,
        decision=decision,
        dimensions=dims,
        dimensionsUsed=used,
        modelVersion=_state.get("version", "unloaded"),
        assessedAt=datetime.now(timezone.utc),
        latencyMs=latency,
    )


# --------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------- #


@app.get("/health")
def health() -> dict:
    return {
        "status": "up" if _state.get("ready") else "degraded",
        "model": _state.get("version", "not loaded"),
        "calibratedDimensions": sorted(
            (_state.get("calset") or CalibrationSet()).calibrators.keys()
        ),
    }


@app.get("/users")
def users() -> dict:
    return {"users": db.list_users(DB_PATH)}


@app.get("/user/{user_id}")
def user_detail(user_id: str) -> dict:
    u = db.get_user(user_id, DB_PATH)
    if u is None:
        raise HTTPException(404, "not enrolled")
    return {
        "userId": u["user_id"],
        "enrolledAt": u["enrolled_at"],
        "keystrokeEnrolled": u["keystroke_tpl"] is not None,
        "mouseEnrolled": u["mouse_tpl"] is not None,
        "shapeEnrolled": u.get("shape_tpl") is not None,
        "eventCount": len(db.get_events(user_id, DB_PATH)),
        "sessionCount": len(db.get_sessions(user_id, DB_PATH)),
    }


@app.get("/history")
def history(userId: str | None = None, limit: int = 25) -> dict:
    return {"verifications": db.get_verifications(userId, limit, DB_PATH)}


@app.get("/")
def index():
    page = STATIC / "index.html"
    if page.exists():
        return FileResponse(page)
    raise HTTPException(404, "frontend not built")
