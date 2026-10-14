from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import numpy as np
from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from amfa.common import artifacts, logs, web
from amfa.common.contract import (FAILED, ID_PATTERN, INSUFFICIENT_DATA, PASSED, SPOOF_DETECTED,
                                  EnrollRequest, EnrollResult, FactorResult, PadMode, PadResult,
                                  VerifyRequest)
from amfa.common.media import MediaError, decode_audio
from amfa.factors.base import ReplayGuard, internal_auth, normalise, store_for
from amfa.factors.voice import models as vm
from amfa.factors.voice.gallery import SpeakerGallery, identify

FACTOR = "VOICE"
MIN_VOICED_S = 1.0
MAX_CLIPPED = 0.01
MIN_SNR_DB = 8.0
ENROLL_CONSISTENCY_MIN = 0.30
MIN_SAMPLES, MAX_SAMPLES = 3, 5

store = store_for("voice")
guard = ReplayGuard(store, identical_cos=0.9999)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logs.setup("voice")
    vm.pad()
    vm.speaker()
    if vm.PROMPT_CHECK != "off":
        vm.asr()
    yield


app = FastAPI(title="AMFA voice factor", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
web.install(app, body_limits=[("/factor/", 32 * 1024 * 1024)])


def _quality_problem(q: dict) -> str | None:
    if q["voicedS"] < MIN_VOICED_S:
        return "NOT_ENOUGH_SPEECH"
    if q["clippedRatio"] > MAX_CLIPPED:
        return "AUDIO_CLIPPED"
    if q["snrDb"] < MIN_SNR_DB:
        return "TOO_NOISY"
    return None


@app.post("/factor/enroll", response_model=EnrollResult, dependencies=[Depends(internal_auth)])
def enroll(req: EnrollRequest) -> EnrollResult:
    t0 = time.perf_counter()
    ms = lambda: int((time.perf_counter() - t0) * 1000)
    samples = req.payload.get("samples")
    if not isinstance(samples, list) or not (MIN_SAMPLES <= len(samples) <= MAX_SAMPLES):
        return EnrollResult(factor=FACTOR, enrolled=False, reason="SAMPLE_COUNT_INVALID", latencyMs=ms())

    decoded = []
    for s in samples:
        try:
            wav, digest, q = decode_audio(s)
        except MediaError as e:
            return EnrollResult(factor=FACTOR, enrolled=False, reason=e.code, latencyMs=ms())
        problem = _quality_problem(q)
        if problem:
            return EnrollResult(factor=FACTOR, enrolled=False, reason=problem, quality=q, latencyMs=ms())
        decoded.append((wav, digest, q))

    digests = [d for _, d, _ in decoded]
    if len(set(digests)) != len(digests):
        return EnrollResult(factor=FACTOR, enrolled=False, reason="DUPLICATE_SAMPLES", latencyMs=ms())

    embs = [vm.speaker().embed(w) for w, _, _ in decoded]
    pads = [vm.pad().score(w) for w, _, _ in decoded]
    sims = [float(embs[i] @ embs[j]) for i in range(len(embs)) for j in range(i + 1, len(embs))]
    if min(sims) < ENROLL_CONSISTENCY_MIN:
        return EnrollResult(factor=FACTOR, enrolled=False, reason="INCONSISTENT_SPEAKER",
                            quality={"minPairSimilarity": round(min(sims), 3)}, latencyMs=ms())

    template = {"voiceprint": normalise(np.mean(embs, axis=0)).tolist(), "model": vm.speaker().version}
    store.put(req.tenantId, req.userId, template,
              meta={"samples": len(embs), "padMean": round(float(np.mean(pads)), 3)})
    for (_, d, _), e in zip(decoded, embs):
        guard.remember(req.tenantId, req.userId, d, e)
    return EnrollResult(factor=FACTOR, enrolled=True, modelVersion=vm.speaker().version,
                        quality={"samples": len(embs), "minPairSimilarity": round(min(sims), 3),
                                 "padScores": [round(p, 3) for p in pads],
                                 "voicedS": [q["voicedS"] for _, _, q in decoded]},
                        latencyMs=ms())


@app.post("/factor/verify", response_model=FactorResult, dependencies=[Depends(internal_auth)])
def verify(req: VerifyRequest) -> FactorResult:
    t0 = time.perf_counter()
    version = f"{vm.pad().version} + {vm.speaker().version}"

    def result(status: str, reason: str | None = None, **kw) -> FactorResult:
        return FactorResult(factor=FACTOR, status=status, failureReason=reason,
                            threshold=vm.MATCH_THRESHOLD, modelVersion=version,
                            latencyMs=int((time.perf_counter() - t0) * 1000), **kw)

    template = store.get(req.tenantId, req.userId)
    if template is None:
        return result(INSUFFICIENT_DATA, "NO_ENROLLED_TEMPLATE")

    try:
        wav, digest, q = decode_audio(req.payload.get("audio", ""))
    except MediaError as e:
        return result(FAILED, e.code)
    problem = _quality_problem(q)
    if problem:
        return result(FAILED, problem, detail={"quality": q})

    hit = guard.check(req.tenantId, req.userId, digest, None)
    if hit:
        return result(SPOOF_DETECTED, hit, livenessPassed=False)

    pad_score = vm.pad().score(wav)
    bonafide = pad_score > vm.PAD_THRESHOLD
    pad = PadResult(ran=True, verdict="BONAFIDE" if bonafide else "SPOOF",
                    score=round(pad_score, 4), threshold=round(vm.PAD_THRESHOLD, 4),
                    model=vm.pad().version, mode=req.padMode)
    common = dict(livenessPassed=bonafide, livenessScore=round(pad_score, 4), pad=pad)
    if not bonafide and req.padMode is PadMode.TERMINAL:
        return result(SPOOF_DETECTED, "PRESENTATION_ATTACK_DETECTED", **common)

    prompt_result = None
    prompt = str(req.challenge.get("prompt", ""))
    if vm.PROMPT_CHECK != "off" and prompt:
        prompt_result = vm.asr().check(wav, prompt)
        prompt_result["mode"] = vm.PROMPT_CHECK
        if not prompt_result["spoken"] and vm.PROMPT_CHECK == "enforce":
            return result(FAILED, "PROMPT_NOT_SPOKEN", detail={"quality": q, "prompt": prompt_result}, **common)

    emb = vm.speaker().embed(wav)
    hit = guard.check(req.tenantId, req.userId, "-", emb)
    if hit:
        return result(SPOOF_DETECTED, hit, **common)
    guard.remember(req.tenantId, req.userId, digest, emb)

    score = float(np.asarray(template["voiceprint"], dtype=np.float32) @ emb)
    detail = {"quality": q}
    if prompt_result is not None:
        detail["prompt"] = prompt_result
    if score < vm.MATCH_THRESHOLD:
        return result(FAILED, "SPEAKER_IDENTITY_MISMATCH", score=round(score, 4), detail=detail, **common)
    reason = None if bonafide else "PAD_ADVISORY_SPOOF_SUSPECTED"
    if prompt_result is not None and not prompt_result["spoken"]:
        reason = "PROMPT_ADVISORY_NOT_SPOKEN" if reason is None else reason + "+PROMPT_NOT_SPOKEN"
    return result(PASSED, reason, score=round(score, 4), detail=detail, **common)


class IdentifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenantId: str = Field(pattern=ID_PATTERN)
    payload: dict[str, Any]
    padMode: PadMode = PadMode.TERMINAL
    excludeUserId: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    topK: int = Field(default=5, ge=1, le=20)


@app.post("/factor/identify", dependencies=[Depends(internal_auth)])
def identify_voice(req: IdentifyRequest) -> dict:
    """1:N open-set identification: which of this tenant's enrolled users, if any, is speaking.
    For the platform only (duplicate-voice and mule-ring checks); never exposed to a customer,
    since the answer names another customer."""
    t0 = time.perf_counter()

    def out(decision: str, reason: str | None = None, **kw) -> dict:
        return {"decision": decision, "identified": decision == "IDENTIFIED", "reason": reason,
                "modelVersion": vm.speaker().version,
                "latencyMs": int((time.perf_counter() - t0) * 1000), **kw}

    try:
        wav, _, q = decode_audio(req.payload.get("audio", ""))
    except MediaError as e:
        return out("REJECTED_INPUT", e.code)
    problem = _quality_problem(q)
    if problem:
        return out("REJECTED_INPUT", problem, quality=q)

    pad_score = vm.pad().score(wav)
    bonafide = pad_score > vm.PAD_THRESHOLD
    pad = {"verdict": "BONAFIDE" if bonafide else "SPOOF", "score": round(pad_score, 4),
           "threshold": round(vm.PAD_THRESHOLD, 4), "mode": req.padMode.value}
    if not bonafide and req.padMode is PadMode.TERMINAL:
        return out("REJECTED_SPOOF", "PRESENTATION_ATTACK_DETECTED", pad=pad)

    emb = vm.speaker().embed(wav)
    gallery = SpeakerGallery(embedding_dim=len(emb))
    for user, tpl in store.all(req.tenantId):
        vec = np.asarray(tpl.get("voiceprint", []), dtype=np.float32)
        if user != req.excludeUserId and tpl.get("model") == vm.speaker().version and len(vec) == len(emb):
            gallery.enroll_speaker(user, vec)

    res = identify(gallery, emb, threshold=vm.IDENT_THRESHOLD, margin=vm.IDENT_MARGIN, top_k=req.topK)
    return out(res["decision"], res["rejection_reason"], pad=pad, identifiedUserId=res["identified_speaker_id"],
               top1Score=res["top1_score"], top2Score=res["top2_score"], scoreMargin=res["score_margin"],
               gallerySize=res["gallery_size"], threshold=res["threshold"], margin=res["margin"],
               candidates=[{"userId": c["speaker_id"], "score": round(c["cosine_similarity"], 4),
                            "rank": c["rank"]} for c in res["candidates"]])


@app.delete("/factor/templates/{tenant_id}/{user_id}", dependencies=[Depends(internal_auth)])
def erase(tenant_id: str, user_id: str) -> dict:
    return {"factor": FACTOR, "deleted": store.delete(tenant_id, user_id)}


@app.get("/health")
def health() -> dict:
    pins = [a for a in artifacts.status() if a["path"].startswith("voice/")]
    return {"service": "voice", "status": "up" if all(a["verified"] for a in pins) else "degraded",
            "models": pins, "matchThreshold": vm.MATCH_THRESHOLD, "padThreshold": vm.PAD_THRESHOLD,
            "identThreshold": vm.IDENT_THRESHOLD, "identMargin": vm.IDENT_MARGIN,
            "padDefaultMode": "advisory", "promptCheck": vm.PROMPT_CHECK, "store": store.ping()}
