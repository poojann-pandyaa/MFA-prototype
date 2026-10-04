"""
The shared factor contract.

Every factor service in the system -- face, voice, behavioural, OTP --
answers /factor/verify with the same response shape and one of the same
five statuses. The orchestrator and decision engine are written against
this shape, so it is defined once here as models rather than assembled by
hand in each endpoint, where it would drift.

UEBA additionally exposes a risk preview, which is not a factor result: it
is consumed by the decision engine before any challenge is shown.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Must match the decision engine's fixed factor enumeration exactly, or its
# validation will reject this factor. Configurable in case the team settles
# on "UEBA" instead.
FACTOR_NAME = os.getenv("UEBA_FACTOR_NAME", "BEHAVIOURAL")

# Score threshold for PASSED. 0.70 on the score scale is the existing LOW
# band boundary, P(impostor) < 0.30. The orchestrator's marginal-pass rule
# (cleared by under 15%) then reads 0.70-0.805 as marginal. Derived from
# simulated calibration: re-derive once real enrollments exist.
THRESHOLD = float(os.getenv("UEBA_FACTOR_THRESHOLD", "0.70"))

MODEL_VERSION = "ueba-behavioural-2.0"


class FactorStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SPOOF_DETECTED = "SPOOF_DETECTED"        # never emitted by UEBA
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNAVAILABLE = "UNAVAILABLE"


class FailureReason(str, Enum):
    BEHAVIOUR_MISMATCH = "BEHAVIOUR_MISMATCH"
    CHALLENGE_INCOMPLETE = "CHALLENGE_INCOMPLETE"
    CHALLENGE_MISSING = "CHALLENGE_MISSING"
    NO_TEMPLATE = "NO_TEMPLATE"
    SERVICE_ERROR = "SERVICE_ERROR"


class PreviewStatus(str, Enum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNAVAILABLE = "UNAVAILABLE"


class Outcome(str, Enum):
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


# --------------------------------------------------------------- capture


class KeyEvent(BaseModel):
    key: str
    down: float
    up: float | None = None


class PointerPoint(BaseModel):
    x: float
    y: float
    t: float


class PointerClick(BaseModel):
    down: float
    up: float | None = None


class SessionPayload(BaseModel):
    start: float
    end: float
    actions: list[float] = []


class Capability(BaseModel):
    pointer: bool = True    # mouse or trackpad
    touch: bool = False     # finger on a touchscreen


class PointerCapture(BaseModel):
    points: list[PointerPoint] = []
    clicks: list[PointerClick] = []


# ------------------------------------------------------------ challenge


class ChallengeResponse(BaseModel):
    challengeId: str
    factor: str
    phrase: str
    shape: list[list[float]]
    aspectRatio: float
    expiresAt: datetime
    shapeRequired: bool


# --------------------------------------------------------------- preview


class PreviewRequest(BaseModel):
    """
    Raw identifiers stop here. The IP address and device ID reach only this
    service, which turns them into flags; the decision layer never sees
    them. No free-text fields: payee names and remarks are not accepted.
    """

    userId: str
    transactionId: str
    amountPaise: int = Field(..., ge=0)
    payeeId: str | None = None
    deviceId: str
    ip: str
    timestamp: datetime | None = None
    channel: str = "WEB"
    action: str = "TRANSFER"
    session: SessionPayload | None = None


class PreviewFlags(BaseModel):
    deviceSeenBefore: bool
    locationSeenBefore: bool
    payeeSeenBefore: bool
    hourUnusual: bool | None = None     # None: too little history to judge
    velocityHigh: bool
    dormant: bool | None = None


class PreviewResponse(BaseModel):
    factor: str
    kind: str = "RISK_PREVIEW"
    status: PreviewStatus
    band: str
    riskScore: float | None
    flags: PreviewFlags
    dimensionsUsed: list[str]
    confidence: float
    modelVersion: str
    source: str = "live"
    latencyMs: int
    detail: dict[str, Any] = {}


# ---------------------------------------------------------------- verify


class FactorPayload(BaseModel):
    keystroke: list[KeyEvent] = []
    shapeTrace: list[PointerPoint] = []
    pointer: PointerCapture = PointerCapture()
    capability: Capability = Capability()


class FactorVerifyRequest(BaseModel):
    sessionId: str
    userId: str
    transactionId: str
    challengeId: str
    payload: FactorPayload = FactorPayload()


class FactorVerifyResponse(BaseModel):
    factor: str
    status: FactorStatus
    score: float | None
    threshold: float
    livenessPassed: bool | None = None   # UEBA has no presentation-attack detector
    livenessScore: float | None = None
    failureReason: FailureReason | None = None
    modelVersion: str
    source: str = "live"
    latencyMs: int
    detail: dict[str, Any] = {}


# ---------------------------------------------------------------- commit


class CommitRequest(BaseModel):
    transactionId: str
    outcome: Outcome


class CommitResponse(BaseModel):
    transactionId: str
    outcome: Outcome
    written: bool
    reason: str | None = None
    eventsNow: int
    sessionsNow: int
