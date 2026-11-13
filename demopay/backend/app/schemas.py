from typing import Dict, Literal, Optional

from pydantic import BaseModel, Field


class SignupIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=100)


class LoginIn(BaseModel):
    username: str
    password: str


class Signals(BaseModel):
    """Phone and network facts. In a real app an attested SDK or the telecom would supply them; in the demo the presenter chooses."""
    call_active: Optional[bool] = None
    call_minutes: Optional[float] = Field(default=None, ge=0)
    call_unknown_number: Optional[bool] = None
    remote_access_active: Optional[bool] = None
    device_bound_hours: Optional[float] = Field(default=None, ge=0)
    old_device_active: Optional[bool] = None
    sim_changed_hours: Optional[float] = Field(default=None, ge=0)
    pin_reset_hours: Optional[float] = Field(default=None, ge=0)
    bio_similarity: Optional[float] = Field(default=None, ge=0, le=1)
    integrity_fail: Optional[bool] = None
    overlay_accessibility: Optional[bool] = None


class RiskIn(BaseModel):
    payee_vpa: str = Field(min_length=3, max_length=100)
    amount: float = Field(gt=0, le=10_000_000)
    channel: Literal["pay", "collect", "qr"] = "pay"
    note: str = Field(default="", max_length=140)
    requester_is_contact: bool = False
    attested: bool = True                    # True: device signals are trusted (full model); False: server-only model
    signals: Signals = Signals()
    base_rate: Optional[float] = Field(default=None, gt=0, lt=0.5)   # demo only


class AnswerIn(BaseModel):
    nonce: str
    acknowledged: Optional[bool] = None
    answers: Optional[Dict[str, str]] = None
    typed: Optional[str] = None
    confirmed: Optional[bool] = None


class PinIn(BaseModel):
    pin: str = Field(pattern=r"^\d{4,6}$")


class PersonaIn(BaseModel):
    name: Literal["asha", "ramesh", "kabir", "meera"]


class MethodIn(BaseModel):
    method: str
    enrolled: bool
