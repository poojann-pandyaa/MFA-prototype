"""
UEBA risk assessment service.

Independent by design: it returns a risk score and takes no position on
what should happen next. No verdict, no factor count. Threshold and policy
decisions belong to the orchestration layer.
"""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from features.engine import MIN_EVENTS, FeatureEngine
from models.base import ModelContext
from models.calibrate import Calibrator
from models.loader import load_model

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "artifacts"))
IP_SALT = os.getenv("IP_SALT", "dev-salt-replace-in-production")
MIN_EVENTS_CFG = int(os.getenv("MIN_EVENTS", str(MIN_EVENTS)))

_state: dict[str, Any] = {}


class Action(str, Enum):
    LOGIN = "LOGIN"
    TRANSFER = "TRANSFER"
    ADD_BENEFICIARY = "ADD_BENEFICIARY"
    CHANGE_LIMIT = "CHANGE_LIMIT"


class Channel(str, Enum):
    WEB = "WEB"
    MOBILE = "MOBILE"


class Status(str, Enum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Band(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AssessRequest(BaseModel):
    """
    No free-text fields. Beneficiary names and remarks are
    attacker-controlled, are needed by no feature, and would create an
    injection path into the decision layer. Excluded by construction.
    """

    userId: str
    amount: float | None = None
    deviceId: str
    ip: str
    timestamp: datetime
    beneficiaryId: str | None = None
    action: Action
    channel: Channel


class AssessResponse(BaseModel):
    riskScore: float | None
    status: Status
    band: Band
    features: dict[str, Any] | None
    eventCount: int
    modelVersion: str
    assessedAt: datetime
    latencyMs: int


def hash_ip(ip: str) -> str:
    """Salted hash. Supports "same network" checks without keeping the IP."""
    return hashlib.sha256((IP_SALT + ip).encode()).hexdigest()[:32]


def derive_city(ip: str) -> str:
    """Placeholder for GeoIP. The address is discarded after this call."""
    return "UNKNOWN"


def band_for(prob: float) -> Band:
    if prob < 0.30:
        return Band.LOW
    if prob < 0.70:
        return Band.MEDIUM
    return Band.HIGH


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model once at startup, not per request."""
    try:
        model = load_model("models/isoforest")
        calibrator = Calibrator.load(ARTIFACTS)
        _state.update(
            model=model,
            calibrator=calibrator,
            engine=FeatureEngine(MIN_EVENTS_CFG),
            version=f"{model.name}-{model.version}",
            ready=True,
        )
    except Exception as exc:  # noqa: BLE001
        _state.update(ready=False, error=str(exc))
    yield


app = FastAPI(title="UEBA Risk Service", version="1.0.0", lifespan=lifespan)


# In-memory event history. Week 3's PostgreSQL schema replaces this; the
# interface (list of past events per user) is the same either way.
_history: dict[str, list[dict]] = {}


@app.post("/risk/assess", response_model=AssessResponse)
def assess(req: AssessRequest) -> AssessResponse:
    started = time.perf_counter()

    # Derive and discard. Neither the raw IP nor anything reconstructable
    # from it proceeds past this point.
    ip_hash = hash_ip(req.ip)
    city = derive_city(req.ip)

    event = {
        "user_id": req.userId,
        "amount": req.amount or 0.0,
        "beneficiary_id": req.beneficiaryId or "",
        "device_id": req.deviceId,
        "ip_hash": ip_hash,
        "city": city,
        "occurred_at": req.timestamp,
    }

    history = _history.setdefault(req.userId, [])
    engine: FeatureEngine = _state.get("engine") or FeatureEngine(MIN_EVENTS_CFG)

    # Cold start is answered before the model is consulted. It needs no
    # scoring, so a missing model must not turn "new user" into an error.
    if not engine.has_sufficient_history(history):
        history.append(event)
        return AssessResponse(
            riskScore=None,
            status=Status.INSUFFICIENT_DATA,
            band=Band.HIGH,          # absence of data is not absence of risk
            features=None,
            eventCount=len(history) - 1,
            modelVersion=_state.get("version", "unloaded"),
            assessedAt=datetime.now(timezone.utc),
            latencyMs=int((time.perf_counter() - started) * 1000),
        )

    if not _state.get("ready"):
        raise HTTPException(
            503,
            f"model not loaded: {_state.get('error')}. "
            "Run `python -m scripts.train` first.",
        )

    fv = engine.compute(event, history)
    frame = pd.DataFrame([fv.as_dict()])

    model = _state["model"]
    ctx = ModelContext(data=frame, artifact_dir=ARTIFACTS)
    model.validate_features(ctx)

    raw_score = model.infer(ctx)
    prob = float(_state["calibrator"].transform(raw_score)[0])

    history.append(event)

    return AssessResponse(
        riskScore=round(prob, 4),
        status=Status.OK,
        band=band_for(prob),
        features=fv.as_dict(),
        eventCount=len(history) - 1,
        modelVersion=_state["version"],
        assessedAt=datetime.now(timezone.utc),
        latencyMs=int((time.perf_counter() - started) * 1000),
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "up" if _state.get("ready") else "degraded",
        "model": _state.get("version", "not loaded"),
    }


@app.get("/model/info")
def model_info() -> dict[str, Any]:
    if not _state.get("ready"):
        raise HTTPException(503, "model not loaded")
    model = _state["model"]
    return {
        "modelVersion": _state["version"],
        "expectedFeatures": model.expected_features,
        "minEvents": MIN_EVENTS_CFG,
        "manifest": getattr(model, "manifest", {}),
    }
