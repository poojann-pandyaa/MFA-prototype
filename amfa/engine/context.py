from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel as _PydanticBase
from pydantic import ConfigDict, Field


class BaseModel(_PydanticBase):
    model_config = ConfigDict(extra="forbid")


class Band(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Factor(str, Enum):
    OTP = "OTP"
    UEBA = "UEBA"
    FACE = "FACE"
    VOICE = "VOICE"


class ActionFloor(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


FLOOR_ORDER = [ActionFloor.L1, ActionFloor.L2, ActionFloor.L3, ActionFloor.L4]


def max_floor(*floors: Optional[ActionFloor]) -> ActionFloor:
    present = [f for f in floors if f is not None]
    return max(present, key=FLOOR_ORDER.index) if present else ActionFloor.L2


FRICTION_ORDER = [Factor.OTP, Factor.UEBA, Factor.FACE, Factor.VOICE]

FLOOR_MINIMUM = {ActionFloor.L1: 1, ActionFloor.L2: 2, ActionFloor.L3: 3, ActionFloor.L4: 99}

SELECTABLE = [Factor.UEBA, Factor.FACE, Factor.VOICE]

CHALLENGE_BIOMETRICS = [Factor.FACE, Factor.VOICE]


def dynamic_factors(permitted) -> list:
    return [Factor.OTP] if Factor.OTP in permitted else [f for f in CHALLENGE_BIOMETRICS if f in permitted]


class TxnType(str, Enum):
    TRANSFER = "TRANSFER"
    PAYMENT = "PAYMENT"
    CARD_NOT_PRESENT = "CARD_NOT_PRESENT"
    ADD_BENEFICIARY = "ADD_BENEFICIARY"
    CHANGE_LIMIT = "CHANGE_LIMIT"
    PROFILE_CHANGE = "PROFILE_CHANGE"
    DEVICE_BINDING = "DEVICE_BINDING"


class Segment(str, Enum):
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"
    SENIOR = "SENIOR"
    CORPORATE = "CORPORATE"
    NEW_TO_BANK = "NEW_TO_BANK"
    WATCHLIST = "WATCHLIST"


class Appetite(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    PERMISSIVE = "PERMISSIVE"


class Regulatory(str, Enum):
    RBI_2025 = "RBI_2025"
    PSD2_SCA = "PSD2_SCA"
    BASELINE = "BASELINE"


class TransactionCtx(BaseModel):
    amountBand: Band
    isNewBeneficiary: bool = False
    beneficiaryAgeDays: Optional[int] = None
    type: TxnType = TxnType.TRANSFER
    # band of this payment plus everything sent in the last 24 h; None = unknown, so no leniency
    exposureBand: Optional[Band] = None


class DeviceCtx(BaseModel):
    deviceKnown: bool = True
    locationKnown: bool = True
    hourUnusual: bool = False
    channel: str = "MOBILE"


class BehaviouralCtx(BaseModel):
    band: Band = Band.LOW
    score: Optional[float] = None
    status: str = "OK"
    dimensionsUsed: list[str] = Field(default_factory=list)
    source: str = "live"
    latencyMs: int = 0
    dormant: bool = False


class Enrollment(BaseModel):
    otp: bool = True
    face: bool = True
    voice: bool = True
    keystroke: bool = True          # behavioural templates for a keyboard and mouse
    shape: bool = True
    keystrokeTouch: bool = False    # and for a touchscreen; a separate set, enrolled on a phone
    shapeTouch: bool = False


class Capability(BaseModel):
    camera: bool = True
    microphone: bool = True
    pointer: bool = True
    touch: bool = False             # the customer is on a touchscreen (phone or tablet)


def behaviour_input(c: Capability) -> str:
    """Which behavioural template set this device is checked against."""
    return "touch" if c.touch else "keyboard"


class OrgCtx(BaseModel):
    regulatory: Regulatory = Regulatory.RBI_2025
    appetite: Appetite = Appetite.BALANCED
    segment: Segment = Segment.STANDARD
    permitted: list[Factor] = Field(default_factory=lambda: list(FRICTION_ORDER))
    required: list[Factor] = Field(default_factory=list)
    agentMaxAdditions: int = Field(default=3, ge=0, le=3)
    reviewOnAnyUnmet: bool = False
    lowValueExempt: bool = False
    neverReview: bool = False


class Policy(BaseModel):
    actionFloor: ActionFloor = ActionFloor.L2
    maxFactors: int = 3
    guidance: Optional[str] = Field(default=None, max_length=1000)
    # the strongest automated checks replace a person unless a hard-hold sign is present (see adjudicate.py)
    lowTouch: bool = True


class Context(BaseModel):
    userId: str
    transactionId: str
    transaction: TransactionCtx
    device: DeviceCtx
    behavioural: BehaviouralCtx
    enrollment: Enrollment = Field(default_factory=Enrollment)
    capability: Capability = Field(default_factory=Capability)
    policy: Policy = Field(default_factory=Policy)
    org: OrgCtx = Field(default_factory=OrgCtx)


class Plan(BaseModel):
    planId: str = ""
    required: list[Factor] = Field(default_factory=lambda: [Factor.OTP])
    order: list[Factor] = Field(default_factory=list)
    riskBand: Band = Band.LOW
    source: str = "rules"
    reasoning: str = ""
    counterfactual: str = ""
    policyFloorApplied: bool = False
    floor: ActionFloor = ActionFloor.L2
    floorMet: bool = True
    escalate: Optional[str] = None
    escalateReason: Optional[str] = None
    # why it escalated: LEVEL (highest threat level), MODEL (risk model down), UNMET (a check can't run),
    # FLOOR (the regulatory minimum can't be met), HOLD (a hard-hold sign). Only the first three can be
    # answered with stronger automated checks instead of a person.
    escalateKind: Optional[str] = None
    autoHandled: Optional[str] = None      # what would have gone to a person, and how it was handled instead
    scamRisk: bool = False                 # a large first payment: the customer gets the scam warning
    unmet: list[Factor] = Field(default_factory=list)
    substituted: dict[str, str] = Field(default_factory=dict)
    rulesBaseline: list[Factor] = Field(default_factory=list)
    orgAdded: list[Factor] = Field(default_factory=list)
    agentAdded: list[Factor] = Field(default_factory=list)
    agentCapped: list[Factor] = Field(default_factory=list)
    agentError: Optional[str] = None
    agentName: str = ""
    agentUsage: dict = Field(default_factory=dict)
    latencyMs: int = 0
    engineVersion: str = "rules-agent-1.0"

    def sequence(self) -> list[Factor]:
        return self.order or [f for f in FRICTION_ORDER if f in self.required]
