from pydantic import BaseModel, Field
from typing import Annotated, List, Literal, Optional
from datetime import datetime

# Per-frame cap on a step-2 image's base64 data URL, in characters. The
# frontend captures 720x540 JPEGs (react-webcam default quality 0.92):
# measured with cv2 at q92, a smooth synthetic frame is ~160K chars and
# pure random noise (the worst case for JPEG) ~507K chars, so 1M chars
# (~750 KB of JPEG) leaves ~2x headroom over the worst case while stopping
# a single multi-megabyte frame. Oversized frames fail request validation
# (422) before any decoding.
# The frame COUNT is bounded separately in main.py (LIVENESS_MAX_FRAMES ->
# 400 "Too many frames"); the overall request body should also be capped at
# the reverse proxy (see README).
MAX_IMAGE_B64_CHARS = 1_000_000

class UserCreate(BaseModel):
    username: str
    password: str
    face_image_b64: str # Base64 encoded image from webcam

class UserLoginStep1(BaseModel):
    username: str
    password: str
    device_identifier: str

class UserLoginStep2(BaseModel):
    username: str
    images: List[Annotated[str, Field(max_length=MAX_IMAGE_B64_CHARS)]]
    device_identifier: str
    session_id: str  # returned by /login/step1; proves step 1 was completed

class LoginHistoryResponse(BaseModel):
    id: int
    timestamp: datetime
    device_identifier: str
    risk_score: str
    face_check_triggered: bool
    success: bool

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str


class TxnRiskRequest(BaseModel):
    payee_vpa: str = Field(min_length=3, max_length=100)
    amount: float = Field(gt=0, le=10_000_000)
    device_identifier: str
    balance: Optional[float] = Field(default=None, gt=0)
    channel: Literal["pay", "collect", "qr"] = "pay"
    # True only when the device signals below come from an attested on-device SDK. False -> the
    # server_only model is used and the device telemetry fields are ignored.
    sdk_attested: bool = False
    # Server-side facts (PSP / telco / backend in production; request fields in this demo).
    device_bound_hours: Optional[float] = Field(default=None, ge=0)   # age of this device's UPI binding
    old_device_active: Optional[bool] = None
    sim_changed_hours: Optional[float] = Field(default=None, ge=0)
    pin_reset_hours: Optional[float] = Field(default=None, ge=0)
    failed_pin_attempts: int = Field(default=0, ge=0, le=20)           # the PSP always knows this; 0 when none
    ip_geo_km: Optional[float] = Field(default=None, ge=0)            # IP-geo distance from usual place
    payee_is_merchant: Optional[bool] = None
    payee_risky_category: Optional[bool] = None                       # crypto / gaming style category
    payee_fri: Optional[Literal["none", "medium", "high", "very_high"]] = None  # government fraud-risk band (stub)
    payee_complaints: Optional[int] = Field(default=None, ge=0)
    # Attested on-device telemetry (spoofable if the device is attacker-controlled).
    remote_access_active: Optional[bool] = None
    bio_similarity: Optional[float] = Field(default=None, ge=0, le=1)
    integrity_fail: Optional[bool] = None
    overlay_accessibility: Optional[bool] = None
    sideload_hours: Optional[float] = Field(default=None, ge=0)
    login_to_pay_s: Optional[float] = Field(default=None, ge=0)
    call_active: Optional[bool] = None                                # flag + minutes only, never numbers or audio
    call_minutes: Optional[float] = Field(default=None, ge=0)
    call_unknown_number: Optional[bool] = None
    requester_is_contact: Optional[bool] = None                       # collect requests: is the requester a saved contact

class StepUp(BaseModel):
    session_id: str
    challenge_type: str

class TxnRiskResponse(BaseModel):
    txn_id: int
    severity: str
    threat_type: str
    action: str
    message: str
    status: str
    hold_until: Optional[datetime] = None
    step_up: Optional[StepUp] = None

class TxnConfirmResponse(BaseModel):
    txn_id: int
    status: str
