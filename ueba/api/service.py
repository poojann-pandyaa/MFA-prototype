"""
UEBA service.

Two roles, at two moments in a transaction:

  POST /risk/preview    before the decision. Transaction and session
                        behaviour only -- nothing is asked of the user.
                        Consumed by the decision engine as input.

  POST /factor/verify   when the engine selects this factor. Keystroke
                        and shape tracing -- the user is challenged.
                        Answers in the shared factor contract.

Plus GET /factor/challenge (single-use challenge before verify) and
POST /profile/commit (the orchestrator reports how the session ended; only
APPROVED transactions enter the user's history).

The transaction dimension deliberately does not count inside the factor
score. It already shaped the decision through the preview, so counting it
again would double-count the same evidence.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api import dimensions as dm
from api.contract import (FACTOR_NAME, MODEL_VERSION, THRESHOLD, ChallengeResponse,
                          CommitRequest, CommitResponse, FactorStatus,
                          FactorVerifyRequest, FactorVerifyResponse, FailureReason,
                          KeyEvent, Outcome, PointerClick, PointerPoint,
                          PreviewFlags, PreviewRequest, PreviewResponse,
                          PreviewStatus, SessionPayload)
from features import keystroke as ks
from features import mouse as ms
from features import session as se
from features import shape as sh
from models.calibrate import Calibrator
from models.loader import load_model
from scoring.combine import CalibrationSet, band, combine
from store import db

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "artifacts"))
IP_SALT = os.getenv("IP_SALT", "dev-salt-replace-in-production")
DB_PATH = Path(os.getenv("DB_PATH", "ueba.db"))
STATIC = Path(__file__).parent.parent / "static"
CHALLENGE_TTL_S = int(os.getenv("UEBA_CHALLENGE_TTL_S", "300"))

# Dimensions fused into the factor score. Mouse is computed and reported
# but excluded: calibrated on simulated traces it measured EER 0.098, and
# in live testing it flagged most genuine attempts as impostor.
FACTOR_DIMENSIONS = set(os.getenv("UEBA_FACTOR_DIMENSIONS", "keystroke,shape").split(","))
PREVIEW_DIMENSIONS = {"transaction", "session"}

_state: dict[str, Any] = {}


def hash_ip(ip: str) -> str:
    """Salted hash: "same network as before" without keeping the address."""
    return hashlib.sha256((IP_SALT + ip).encode()).hexdigest()[:32]


def derive_city(ip: str) -> str:
    """Placeholder for GeoIP. The address is discarded after this call."""
    octet = ip.split(".")[0] if "." in ip else "0"
    cities = ["Bangalore", "Mumbai", "Delhi", "Chennai", "Hyderabad"]
    try:
        return cities[int(octet) % len(cities)]
    except ValueError:
        return "UNKNOWN"


def _dump(items) -> list[dict]:
    return [i.model_dump() for i in items]


def _calset() -> CalibrationSet:
    return _state.get("calset") or CalibrationSet()


def _fuse(probs: dict[str, float | None]) -> tuple[float, list[str]]:
    cs = _calset()
    return combine(probs, reliability={d: cs.reliability(d) for d in probs})


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(DB_PATH)
    try:
        model = load_model("models/isoforest")
        _state.update(model=model, tx_calibrator=Calibrator.load(ARTIFACTS),
                      calset=CalibrationSet.load(ARTIFACTS),
                      version=f"{model.name}-{model.version}", ready=True)
    except Exception as exc:  # noqa: BLE001
        _state.update(ready=False, error=str(exc),
                      calset=CalibrationSet.load(ARTIFACTS), version="unloaded")
    yield


app = FastAPI(title="UEBA Service", version="3.0.0", lifespan=lifespan)


# ===================================================================== #
# Enrollment
# ===================================================================== #


class EnrollRequest(BaseModel):
    userId: str
    keystrokeSamples: list[list[KeyEvent]] = Field(..., min_length=1)
    shapeTraces: list[list[PointerPoint]] = []
    mouseSegments: list[list[PointerPoint]] = []
    mouseClicks: list[PointerClick] = []


@app.get("/enroll/phrase")
def enroll_phrase() -> dict:
    """What enrollment needs. Fixed text: random text breaks the comparison."""
    return {
        "phrase": ks.ENROLL_PHRASE,
        "repetitions": ks.TARGET_SAMPLES,
        "discarded": ks.DISCARD_FIRST,
        "minimum": ks.MIN_SAMPLES + ks.DISCARD_FIRST,
        "shape": [list(p) for p in sh.SHAPE],
        "aspectRatio": sh.ASPECT,
        "shapeTracesRequired": sh.MIN_SAMPLES,
        "mousePointsRequired": ms.MIN_POINTS * ms.MIN_SAMPLES,
    }


def _mouse_template(segments: list[list[dict]], clicks: list[dict]):
    """One segment per typing round; clicks matched to their segment by time."""
    samples = []
    for pts in segments:
        if len(pts) < ms.MIN_POINTS:
            continue
        lo, hi = pts[0]["t"], pts[-1]["t"]
        samples.append(ms.extract(pts, [c for c in clicks if lo <= c["down"] <= hi]))
    return ms.build_template(samples)


@app.post("/enroll")
def enroll(req: EnrollRequest) -> dict:
    samples = [ks.extract(_dump(s)) for s in req.keystrokeSamples]
    keystroke_tpl = ks.build_template(samples)
    if keystroke_tpl is None:
        complete = sum(s["coverage"] >= ks.MIN_ENROLL_COVERAGE for s in samples)
        raise HTTPException(400, (
            f"need {ks.MIN_SAMPLES + ks.DISCARD_FIRST} complete typings of the phrase; "
            f"got {complete} of {len(samples)}"))

    traces = [sh.extract(_dump(t)) for t in req.shapeTraces]
    shape_tpl = sh.build_template(traces)
    if shape_tpl is None:
        complete = sum(t["coverage"] >= sh.MIN_ENROLL_COVERAGE for t in traces)
        raise HTTPException(400, (
            f"need {sh.MIN_SAMPLES} complete shape traces, green dot to red dot; "
            f"got {complete} of {len(traces)}"))

    clicks = _dump(req.mouseClicks)
    mouse_tpl = _mouse_template([_dump(s) for s in req.mouseSegments], clicks)
    total_points = sum(len(s) for s in req.mouseSegments)

    db.save_user(req.userId, keystroke_tpl, mouse_tpl, DB_PATH, shape_tpl=shape_tpl)

    dwell = [v for v in keystroke_tpl["dwell_mean"] if v is not None]
    return {
        "userId": req.userId,
        "enrolled": True,
        "keystroke": {"status": "ENROLLED", "samples": keystroke_tpl["n_samples"],
                      "recorded": len(samples), "discarded": ks.DISCARD_FIRST,
                      "positions": keystroke_tpl["n_dwell"],
                      "meanDwellMs": round(float(np.mean(dwell)), 1) if dwell else None,
                      "typingSpeedCps": round(keystroke_tpl["speed_mean"], 2)},
        "shape": {"status": "ENROLLED", "samples": shape_tpl["n_samples"],
                  "checkpoints": shape_tpl["n_checkpoints"]},
        "mouse": ({"status": "ENROLLED", "samples": mouse_tpl["n_samples"],
                   "pointsCaptured": total_points, "excludedFromScore": True}
                  if mouse_tpl else
                  {"status": "NOT_ENROLLED", "pointsCaptured": total_points,
                   "pointsRequired": ms.MIN_POINTS * ms.MIN_SAMPLES,
                   "excludedFromScore": True}),
        "session": {"status": "ACCUMULATES",
                    "reason": "longitudinal; cannot be captured in one sitting"},
    }


# ===================================================================== #
# Contract: challenge, preview, verify, commit
# ===================================================================== #


@app.get("/factor/challenge", response_model=ChallengeResponse)
def factor_challenge(userId: str) -> ChallengeResponse:
    """
    Issue a single-use challenge.

    The phrase and shape stay fixed -- random text would break the
    position-by-position comparison. Replay protection comes from the
    challenge ID instead: a captured submission cannot be sent again.
    """
    if db.get_user(userId, DB_PATH) is None:
        raise HTTPException(404, f"user {userId} is not enrolled")
    cid = f"ch_{uuid.uuid4().hex[:16]}"
    expires = db.issue_challenge(cid, userId, CHALLENGE_TTL_S, DB_PATH)
    return ChallengeResponse(
        challengeId=cid, factor=FACTOR_NAME, phrase=ks.ENROLL_PHRASE,
        shape=[list(p) for p in sh.SHAPE], aspectRatio=sh.ASPECT,
        expiresAt=datetime.fromtimestamp(expires, timezone.utc), shapeRequired=True)


@app.post("/risk/preview", response_model=PreviewResponse)
def risk_preview(req: PreviewRequest) -> PreviewResponse:
    """
    Behavioural risk before any challenge. Writes nothing to history: the
    context is held until /profile/commit reports an approved outcome.
    """
    started = time.perf_counter()
    try:
        occurred = (req.timestamp or datetime.now(timezone.utc)).timestamp()
        city = derive_city(req.ip)
        event = {"user_id": req.userId, "amount": req.amountPaise / 100.0,
                 "beneficiary_id": req.payeeId or "", "device_id": req.deviceId,
                 "city": city, "occurred_at": occurred}

        history = db.get_events(req.userId, DB_PATH)
        flags = dm.preview_flags(event, history)

        tx_dim, p_tx = dm.score_transaction(event, history, _state, ARTIFACTS)
        session_feats = se.extract(req.session.model_dump()) if req.session else None
        se_dim, p_se = dm.score_session(session_feats, db.get_sessions(req.userId, DB_PATH), _calset())

        risk, used = _fuse({"transaction": p_tx, "session": p_se})
        confs = [d.get("confidence", 0.0) for d, p in ((tx_dim, p_tx), (se_dim, p_se)) if p is not None]

        if used:
            status, risk_band, risk_out = PreviewStatus.OK, band(risk), round(risk, 4)
        else:
            # No baseline yet. Absence of evidence is not evidence of safety.
            status, risk_band, risk_out = PreviewStatus.INSUFFICIENT_DATA, "HIGH", None

        db.put_pending(req.transactionId, req.userId, {
            "user_id": req.userId, "event_type": req.action,
            "amount": event["amount"], "beneficiary_id": req.payeeId,
            "device_id": req.deviceId, "ip_hash": hash_ip(req.ip), "city": city,
            "channel": req.channel, "occurred_at": occurred,
        }, session_feats, DB_PATH)

        latency = int((time.perf_counter() - started) * 1000)
        detail = {"transaction": tx_dim, "session": se_dim, "eventsSeen": len(history)}
        db.add_verification(req.userId, risk_out, risk_band, "PREVIEW",
                            {"kind": "preview", "transactionId": req.transactionId,
                             "flags": flags, **detail},
                            MODEL_VERSION, latency, DB_PATH)
        return PreviewResponse(
            factor=FACTOR_NAME, status=status, band=risk_band, riskScore=risk_out,
            flags=PreviewFlags(**flags), dimensionsUsed=used,
            confidence=round(min(confs), 2) if confs else 0.0,
            modelVersion=MODEL_VERSION, latencyMs=latency, detail=detail)
    except Exception as exc:  # noqa: BLE001
        return PreviewResponse(
            factor=FACTOR_NAME, status=PreviewStatus.UNAVAILABLE, band="HIGH",
            riskScore=None,
            flags=PreviewFlags(deviceSeenBefore=False, locationSeenBefore=False,
                               payeeSeenBefore=False, velocityHigh=False),
            dimensionsUsed=[], confidence=0.0, modelVersion=MODEL_VERSION,
            latencyMs=int((time.perf_counter() - started) * 1000),
            detail={"error": type(exc).__name__})


def _factor_verdict(dims: dict[str, dict], score: float | None):
    """Status and failure reason from the challenge dimensions and the score."""
    statuses = [d["status"] for name, d in dims.items()
                if name in FACTOR_DIMENSIONS and d["status"] not in
                ("NOT_APPLICABLE", "INSUFFICIENT_DATA")]
    if not statuses or score is None:
        return FactorStatus.INSUFFICIENT_DATA, FailureReason.NO_TEMPLATE
    if all(s == "MISSING" for s in statuses):
        return FactorStatus.FAILED, FailureReason.CHALLENGE_MISSING
    if "MISSING" in statuses:
        # Skipping a whole part of the challenge cannot be passed, however
        # well the other part went. Otherwise that part would be optional
        # in practice.
        return FactorStatus.FAILED, FailureReason.CHALLENGE_INCOMPLETE
    if score >= THRESHOLD:
        return FactorStatus.PASSED, None
    if "PARTIAL" in statuses:
        return FactorStatus.FAILED, FailureReason.CHALLENGE_INCOMPLETE
    return FactorStatus.FAILED, FailureReason.BEHAVIOUR_MISMATCH


@app.post("/factor/verify", response_model=FactorVerifyResponse)
def factor_verify(req: FactorVerifyRequest) -> FactorVerifyResponse:
    started = time.perf_counter()

    user = db.get_user(req.userId, DB_PATH)
    if user is None:
        return FactorVerifyResponse(
            factor=FACTOR_NAME, status=FactorStatus.INSUFFICIENT_DATA, score=None,
            threshold=THRESHOLD, failureReason=FailureReason.NO_TEMPLATE,
            modelVersion=MODEL_VERSION, latencyMs=0)

    # A protocol violation, not a factor result: answered as an HTTP error.
    check = db.consume_challenge(req.challengeId, req.userId, DB_PATH)
    if check != "OK":
        raise HTTPException(409, f"challenge rejected: {check}")

    try:
        cs = _calset()
        p = req.payload
        can_trace = p.capability.pointer or p.capability.touch

        dims: dict[str, dict] = {}
        probs: dict[str, float | None] = {}
        dims["keystroke"], probs["keystroke"] = dm.score_keystroke(
            user["keystroke_tpl"], _dump(p.keystroke), cs)
        dims["shape"], probs["shape"] = dm.score_shape(
            user.get("shape_tpl"), _dump(p.shapeTrace), cs, applicable=can_trace)
        dims["mouse"], probs["mouse"] = dm.score_mouse(
            user["mouse_tpl"], _dump(p.pointer.points), _dump(p.pointer.clicks), cs)
        dims["mouse"]["excludedFromScore"] = "mouse" not in FACTOR_DIMENSIONS

        fused = {d: probs[d] for d in FACTOR_DIMENSIONS if d in probs}
        p_imp, used = _fuse(fused)
        score = round(1.0 - p_imp, 4) if used else None
        status, reason = _factor_verdict(dims, score)

        latency = int((time.perf_counter() - started) * 1000)
        detail = {**dims, "dimensionsUsed": used,
                  "impostorProbability": round(p_imp, 4) if used else None}
        db.add_verification(req.userId, detail["impostorProbability"],
                            band(p_imp) if used else "HIGH", status.value,
                            {"kind": "factor", "transactionId": req.transactionId,
                             "sessionId": req.sessionId, "score": score,
                             "failureReason": reason.value if reason else None, **detail},
                            MODEL_VERSION, latency, DB_PATH)
        return FactorVerifyResponse(
            factor=FACTOR_NAME, status=status, score=score, threshold=THRESHOLD,
            failureReason=reason, modelVersion=MODEL_VERSION, latencyMs=latency,
            detail=detail)
    except Exception as exc:  # noqa: BLE001
        return FactorVerifyResponse(
            factor=FACTOR_NAME, status=FactorStatus.UNAVAILABLE, score=None,
            threshold=THRESHOLD, failureReason=FailureReason.SERVICE_ERROR,
            modelVersion=MODEL_VERSION,
            latencyMs=int((time.perf_counter() - started) * 1000),
            detail={"error": type(exc).__name__})


@app.post("/profile/commit", response_model=CommitResponse)
def profile_commit(req: CommitRequest) -> CommitResponse:
    """
    Called by the orchestrator once the session resolves. Only APPROVED
    writes the transaction and session into the user's history.
    """
    pend = db.get_pending(req.transactionId, DB_PATH)
    if pend is None:
        raise HTTPException(404, "no preview recorded for this transaction")
    uid = pend["user_id"]

    def counts():
        return len(db.get_events(uid, DB_PATH)), len(db.get_sessions(uid, DB_PATH))

    if pend["committed"]:
        e, s = counts()
        return CommitResponse(transactionId=req.transactionId, outcome=req.outcome,
                              written=False, eventsNow=e, sessionsNow=s,
                              reason=f"already committed as {pend['committed']}")
    if req.outcome != Outcome.APPROVED:
        db.mark_committed(req.transactionId, req.outcome.value, DB_PATH)
        e, s = counts()
        return CommitResponse(transactionId=req.transactionId, outcome=req.outcome,
                              written=False, eventsNow=e, sessionsNow=s,
                              reason="only approved transactions update the profile")

    ev = pend["event"]
    db.add_event(ev["user_id"], ev["event_type"], ev["amount"], ev["beneficiary_id"],
                 ev["device_id"], ev["ip_hash"], ev["city"], ev["channel"],
                 ev["occurred_at"], DB_PATH)
    if pend["session"]:
        db.add_session(uid, pend["session"], DB_PATH)
    db.mark_committed(req.transactionId, Outcome.APPROVED.value, DB_PATH)
    e, s = counts()
    return CommitResponse(transactionId=req.transactionId, outcome=req.outcome,
                          written=True, eventsNow=e, sessionsNow=s)


# ===================================================================== #
# Legacy combined endpoint (deprecated -- use the contract endpoints)
# ===================================================================== #


class VerifyRequest(BaseModel):
    userId: str
    amount: float | None = None
    deviceId: str
    ip: str
    timestamp: datetime | None = None
    beneficiaryId: str | None = None
    action: str = "TRANSFER"
    channel: str = "WEB"
    keystrokeSample: list[KeyEvent] | None = None
    shapeTrace: list[PointerPoint] | None = None
    mousePoints: list[PointerPoint] = []
    mouseClicks: list[PointerClick] = []
    session: SessionPayload | None = None


@app.post("/verify", deprecated=True)
def verify_legacy(req: VerifyRequest) -> dict:
    """
    All dimensions in one call, as before. Kept for clients not yet moved
    to the contract. History is written only when the result is ALLOW, so
    this path no longer feeds failed attempts into the profile either.
    """
    started = time.perf_counter()
    user = db.get_user(req.userId, DB_PATH)
    if user is None:
        raise HTTPException(404, f"user {req.userId} is not enrolled")
    cs = _calset()
    occurred = (req.timestamp or datetime.now(timezone.utc)).timestamp()
    city = derive_city(req.ip)
    event = {"user_id": req.userId, "amount": req.amount or 0.0,
             "beneficiary_id": req.beneficiaryId or "", "device_id": req.deviceId,
             "city": city, "occurred_at": occurred}

    dims, probs = {}, {}
    dims["keystroke"], probs["keystroke"] = dm.score_keystroke(
        user["keystroke_tpl"], _dump(req.keystrokeSample or []), cs)
    dims["shape"], probs["shape"] = dm.score_shape(
        user.get("shape_tpl"), _dump(req.shapeTrace or []), cs)
    dims["mouse"], probs["mouse"] = dm.score_mouse(
        user["mouse_tpl"], _dump(req.mousePoints), _dump(req.mouseClicks), cs)
    feats = se.extract(req.session.model_dump()) if req.session else None
    dims["session"], probs["session"] = dm.score_session(
        feats, db.get_sessions(req.userId, DB_PATH), cs)
    dims["transaction"], probs["transaction"] = dm.score_transaction(
        event, db.get_events(req.userId, DB_PATH), _state, ARTIFACTS)

    risk, used = _fuse({d: p for d, p in probs.items() if d != "mouse"})
    risk_band = band(risk) if used else "HIGH"
    decision = {"LOW": "ALLOW", "MEDIUM": "STEP_UP", "HIGH": "DENY"}[risk_band] if used else "STEP_UP"
    if decision == "ALLOW":
        db.add_event(req.userId, req.action, req.amount, req.beneficiaryId,
                     req.deviceId, hash_ip(req.ip), city, req.channel, occurred, DB_PATH)
        if feats:
            db.add_session(req.userId, feats, DB_PATH)
    latency = int((time.perf_counter() - started) * 1000)
    db.add_verification(req.userId, round(risk, 4) if used else None, risk_band,
                        decision, {"kind": "legacy", **dims}, MODEL_VERSION, latency, DB_PATH)
    return {"userId": req.userId, "riskScore": round(risk, 4) if used else None,
            "band": risk_band, "decision": decision, "dimensions": dims,
            "dimensionsUsed": used, "modelVersion": MODEL_VERSION, "latencyMs": latency}


# ===================================================================== #
# Inspection
# ===================================================================== #


@app.get("/health")
def health() -> dict:
    return {
        "status": "up" if _state.get("ready") else "degraded",
        "factor": FACTOR_NAME,
        "threshold": THRESHOLD,
        "model": _state.get("version", "not loaded"),
        "modelVersion": MODEL_VERSION,
        "calibratedDimensions": sorted(_calset().calibrators.keys()),
        "factorDimensions": sorted(FACTOR_DIMENSIONS),
    }


@app.get("/users")
def users() -> dict:
    return {"users": db.list_users(DB_PATH)}


@app.get("/user/{user_id}")
def user_detail(user_id: str) -> dict:
    u = db.get_user(user_id, DB_PATH)
    if u is None:
        raise HTTPException(404, "not enrolled")
    shape_tpl = u.get("shape_tpl")
    return {
        "userId": u["user_id"], "enrolledAt": u["enrolled_at"],
        "keystrokeEnrolled": u["keystroke_tpl"] is not None,
        "shapeEnrolled": bool(shape_tpl) and "split_mean" in shape_tpl,
        "mouseEnrolled": u["mouse_tpl"] is not None,
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
