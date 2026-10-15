from __future__ import annotations

import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import Depends, FastAPI

from amfa.common import artifacts, logs, web
from amfa.common.contract import (FAILED, INSUFFICIENT_DATA, PASSED, SPOOF_DETECTED,
                                  EnrollRequest, EnrollResult, FactorResult, PadMode, PadResult,
                                  VerifyRequest)
from amfa.common.media import MediaError, decode_image
from amfa.factors.base import ReplayGuard, internal_auth, normalise, store_for
from amfa.factors.face import gesture
from amfa.factors.face import matcher as fm
from amfa.factors.face import pad as fpad

FACTOR = "FACE"
LIVENESS_THRESHOLD = 0.80
LIVENESS_FRAME_FLOOR = 0.50
FRONTAL_MAX_YAW = 0.12
TURN_MIN_DELTA = 0.18
TURN_MIN_IN_BETWEEN = 2
TURNED_IDENTITY_MIN = fm.MATCH_THRESHOLD
BURST_IDENTITY_MIN = fm.MATCH_THRESHOLD
ENROLL_CONSISTENCY_MIN = 0.50
ENROLL_FRAMES = (3, 8)
VERIFY_FRAMES = (6, 24)
MIN_LIVE_FRAMES = 6

store = store_for("face")
guard = ReplayGuard(store, identical_cos=0.9995)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logs.setup("face")
    fpad.get()
    fm.get()
    yield


app = FastAPI(title="AMFA face factor", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
web.install(app, body_limits=[("/factor/", 20 * 1024 * 1024)])


def _frames(payload: dict, bounds: tuple[int, int]) -> list[tuple[np.ndarray, str]]:
    frames = payload.get("frames")
    if not isinstance(frames, list) or not (bounds[0] <= len(frames) <= bounds[1]):
        raise MediaError("FRAME_COUNT_INVALID")
    return [decode_image(f) for f in frames]


def _single_face(img) -> fm.DetectedFace | str:
    faces = fm.get().detect(img)
    if not faces:
        return "NO_FACE_DETECTED"
    if len(faces) > 1:
        return "MULTIPLE_FACES"
    f = faces[0]
    if f.width < fm.MIN_FACE_PX:
        return "FACE_TOO_SMALL"
    return f


@app.post("/factor/enroll", response_model=EnrollResult, dependencies=[Depends(internal_auth)])
def enroll(req: EnrollRequest) -> EnrollResult:
    t0 = time.perf_counter()
    ms = lambda: int((time.perf_counter() - t0) * 1000)
    try:
        frames = _frames(req.payload, ENROLL_FRAMES)
    except MediaError as e:
        return EnrollResult(factor=FACTOR, enrolled=False, reason=e.code, latencyMs=ms())

    digests = [d for _, d in frames]
    if len(set(digests)) != len(digests):
        return EnrollResult(factor=FACTOR, enrolled=False, reason="DUPLICATE_FRAMES", latencyMs=ms())

    embs, live = [], []
    for img, _ in frames:
        f = _single_face(img)
        if isinstance(f, str):
            return EnrollResult(factor=FACTOR, enrolled=False, reason=f, latencyMs=ms())
        p = fpad.get().score(img)
        live.append(p.live_prob)
        embs.append(fm.get().embed(img, f))

    live_mean = float(np.mean(live))
    if live_mean < LIVENESS_THRESHOLD or min(live) < LIVENESS_FRAME_FLOOR:
        return EnrollResult(factor=FACTOR, enrolled=False, reason="LIVENESS_NOT_ESTABLISHED",
                            quality={"livenessMean": round(live_mean, 3)}, latencyMs=ms())

    sims = [fm.cosine(embs[i], embs[j]) for i in range(len(embs)) for j in range(i + 1, len(embs))]
    if min(sims) < ENROLL_CONSISTENCY_MIN:
        return EnrollResult(factor=FACTOR, enrolled=False, reason="INCONSISTENT_IDENTITY",
                            quality={"minPairSimilarity": round(min(sims), 3)}, latencyMs=ms())

    template = {"mean": normalise(np.mean(embs, axis=0)).tolist(),
                "samples": [e.tolist() for e in embs[:5]],
                "model": fm.get().version}
    store.put(req.tenantId, req.userId, template,
              meta={"frames": len(embs), "liveness": round(live_mean, 3)})
    for img_digest, e in zip(digests, embs):
        guard.remember(req.tenantId, req.userId, img_digest, e)
    return EnrollResult(factor=FACTOR, enrolled=True, modelVersion=fm.get().version,
                        quality={"frames": len(embs), "livenessMean": round(live_mean, 3),
                                 "minPairSimilarity": round(min(sims), 3)},
                        latencyMs=ms())


@app.post("/factor/verify", response_model=FactorResult, dependencies=[Depends(internal_auth)])
def verify(req: VerifyRequest) -> FactorResult:
    t0 = time.perf_counter()
    version = f"{fpad.get().version} + {fm.get().version}"

    def result(status: str, reason: str | None = None, **kw) -> FactorResult:
        return FactorResult(factor=FACTOR, status=status, failureReason=reason,
                            threshold=round(fm.MATCH_THRESHOLD, 4), modelVersion=version,
                            latencyMs=int((time.perf_counter() - t0) * 1000), **kw)

    template = store.get(req.tenantId, req.userId)
    if template is None:
        return result(INSUFFICIENT_DATA, "NO_ENROLLED_TEMPLATE")

    action = str(req.challenge.get("action", ""))
    if action not in ("TURN_LEFT", "TURN_RIGHT"):
        return result(FAILED, "CHALLENGE_MISSING")

    try:
        frames = _frames(req.payload, VERIFY_FRAMES)
    except MediaError as e:
        return result(FAILED, e.code)

    digests = [d for _, d in frames]
    if len(set(digests)) != len(digests):
        return result(SPOOF_DETECTED, "REPLAY_DUPLICATE_FRAMES", livenessPassed=False)
    for d in digests:
        hit = guard.check(req.tenantId, req.userId, d, None)
        if hit:
            return result(SPOOF_DETECTED, hit, livenessPassed=False)

    faces, lives = [], []
    for img, _ in frames:
        f = _single_face(img)
        if isinstance(f, str):
            return result(FAILED, f)
        faces.append(f)
        lives.append(fpad.get().score(img).live_prob)

    live_mean = float(np.mean(lives))
    bonafide = live_mean >= LIVENESS_THRESHOLD and min(lives) >= LIVENESS_FRAME_FLOOR
    pad = PadResult(ran=True, verdict="BONAFIDE" if bonafide else "SPOOF",
                    score=round(live_mean, 4), threshold=LIVENESS_THRESHOLD,
                    model=fpad.get().version, mode=req.padMode)
    common = dict(livenessPassed=bonafide, livenessScore=round(live_mean, 4), pad=pad)
    if not bonafide and req.padMode is PadMode.TERMINAL:
        return result(SPOOF_DETECTED, "PRESENTATION_ATTACK_DETECTED", **common)

    live_idx = [i for i, p in enumerate(lives) if p >= LIVENESS_FRAME_FLOOR]
    active = {"action": action, "yaws": [round(f.yaw, 3) for f in faces],
              "liveFrames": len(live_idx), "needFrontalMax": FRONTAL_MAX_YAW,
              "needTurnDelta": TURN_MIN_DELTA, "frameLiveness": [round(float(v), 3) for v in lives]}
    if len(live_idx) < MIN_LIVE_FRAMES:
        return result(FAILED, "NOT_ENOUGH_LIVE_FRAMES", detail={"active": active}, **common)

    # Gesture and identity evidence come only from frames PAD judged live, so photo frames
    # spliced into a live burst can neither fake the turn nor stand in for the face match.
    reason, turn = gesture.check_turn([faces[i].yaw for i in live_idx], action,
                                      frontal_max=FRONTAL_MAX_YAW, min_delta=TURN_MIN_DELTA,
                                      min_in_between=TURN_MIN_IN_BETWEEN)
    if turn is not None:
        active.update(turnDelta=round(turn.displacement, 3), inBetweenFrames=turn.in_between)
    if reason:
        return result(FAILED, reason, detail={"active": active}, **common)

    frontal_idx = [i for i in live_idx if abs(faces[i].yaw) <= FRONTAL_MAX_YAW]
    active["frontalFrames"] = len(frontal_idx)
    if not frontal_idx:
        return result(FAILED, "NO_FRONTAL_FRAME", detail={"active": active}, **common)

    mean_tpl = np.asarray(template["mean"], dtype=np.float32)
    embs = {i: fm.get().embed(frames[i][0], faces[i]) for i in live_idx}
    scores = [fm.cosine(mean_tpl, embs[i]) for i in frontal_idx]
    best = int(np.argmax(scores))
    score = float(scores[best])
    probe = embs[frontal_idx[best]]

    hit = guard.check(req.tenantId, req.userId, "-", probe)
    if hit:
        return result(SPOOF_DETECTED, hit, score=round(score, 4), **common)

    sign = 1.0 if action == "TURN_LEFT" else -1.0
    turned_idx = [i for i in live_idx if sign * (faces[i].yaw - turn.start) >= TURN_MIN_DELTA]
    if not turned_idx:
        return result(FAILED, "HEAD_TURN_NOT_OBSERVED", detail={"active": active}, **common)
    t_i = min(turned_idx, key=lambda i: sign * (faces[i].yaw - turn.start))
    active["turnedFrame"] = t_i
    turned_sim = min(fm.cosine(probe, embs[t_i]), fm.cosine(mean_tpl, embs[t_i]))
    active["turnedIdentity"] = round(turned_sim, 3)
    continuity = min(fm.cosine(probe, e) for e in embs.values())
    active["burstIdentity"] = round(continuity, 3)

    for d, e in zip(digests, [probe] + [None] * (len(digests) - 1)):
        guard.remember(req.tenantId, req.userId, d, e)

    detail = {"active": active, "frontalScores": [round(s, 4) for s in scores]}
    if turned_sim < TURNED_IDENTITY_MIN or continuity < BURST_IDENTITY_MIN:
        return result(FAILED, "IDENTITY_CHANGED_DURING_TURN", score=round(score, 4),
                      detail=detail, **common)
    if score < fm.MATCH_THRESHOLD:
        return result(FAILED, "FACE_MISMATCH", score=round(score, 4), detail=detail, **common)
    return result(PASSED, None if bonafide else "PAD_ADVISORY_SPOOF_SUSPECTED",
                  score=round(score, 4), detail=detail, **common)


@app.delete("/factor/templates/{tenant_id}/{user_id}", dependencies=[Depends(internal_auth)])
def erase(tenant_id: str, user_id: str) -> dict:
    return {"factor": FACTOR, "deleted": store.delete(tenant_id, user_id)}


@app.get("/health")
def health() -> dict:
    face_pins = [a for a in artifacts.status() if a["path"].startswith("face/")]
    return {"service": "face", "status": "up" if all(a["verified"] for a in face_pins) else "degraded",
            "models": face_pins, "matchThreshold": fm.MATCH_THRESHOLD,
            "livenessThreshold": LIVENESS_THRESHOLD, "store": store.ping()}
