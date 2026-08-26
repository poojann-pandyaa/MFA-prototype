from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

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
    face_image_b64: str
    device_identifier: str

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
