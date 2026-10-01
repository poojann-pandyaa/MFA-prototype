from pydantic import BaseModel, Field
from typing import Annotated, List, Optional
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
