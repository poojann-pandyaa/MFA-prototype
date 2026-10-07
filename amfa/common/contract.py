from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

PASSED = "PASSED"
FAILED = "FAILED"
SPOOF_DETECTED = "SPOOF_DETECTED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
UNAVAILABLE = "UNAVAILABLE"

STATUSES = (PASSED, FAILED, SPOOF_DETECTED, INSUFFICIENT_DATA, UNAVAILABLE)
TERMINAL = frozenset({SPOOF_DETECTED})

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"


class PadMode(str, Enum):
    ADVISORY = "advisory"
    TERMINAL = "terminal"


class PadResult(BaseModel):
    ran: bool = False
    verdict: Optional[str] = None
    score: Optional[float] = None
    threshold: Optional[float] = None
    model: str = ""
    mode: PadMode = PadMode.ADVISORY


class FactorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: str
    status: str
    score: Optional[float] = None
    threshold: Optional[float] = None
    livenessPassed: Optional[bool] = None
    livenessScore: Optional[float] = None
    failureReason: Optional[str] = None
    modelVersion: str = ""
    latencyMs: int = 0
    source: str = "live"
    pad: PadResult = Field(default_factory=PadResult)
    detail: dict[str, Any] = Field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.model_dump(mode="json")


class EnrollResult(BaseModel):
    factor: str
    enrolled: bool
    reason: Optional[str] = None
    modelVersion: str = ""
    quality: dict[str, Any] = Field(default_factory=dict)
    latencyMs: int = 0


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenantId: str = Field(pattern=ID_PATTERN)
    userId: str = Field(pattern=ID_PATTERN)
    sessionId: str = Field(pattern=ID_PATTERN)
    transactionId: str = Field(pattern=ID_PATTERN)
    challenge: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]
    padMode: PadMode = PadMode.ADVISORY


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenantId: str = Field(pattern=ID_PATTERN)
    userId: str = Field(pattern=ID_PATTERN)
    payload: dict[str, Any]


def unavailable(factor: str, reason: str, latency_ms: int = 0) -> FactorResult:
    return FactorResult(factor=factor, status=UNAVAILABLE, failureReason=reason,
                        latencyMs=latency_ms, source="unavailable")
