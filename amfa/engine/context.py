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
    # the phone proves itself: a key in its secure hardware, unlocked by the phone's own fingerprint or screen lock, signs a fresh
    # challenge. Possession plus the person who unlocked it, with nothing to set up beyond the phone itself.
    DEVICE = "DEVICE"


class ActionFloor(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


FLOOR_ORDER = [ActionFloor.L1, ActionFloor.L2, ActionFloor.L3, ActionFloor.L4]


def max_floor(*floors: Optional[ActionFloor]) -> ActionFloor:
    present = [f for f in floors if f is not None]
    return max(present, key=FLOOR_ORDER.index) if present else ActionFloor.L2


FRICTION_ORDER = [Factor.OTP, Factor.DEVICE, Factor.UEBA, Factor.FACE, Factor.VOICE]

FLOOR_MINIMUM = {ActionFloor.L1: 1, ActionFloor.L2: 2, ActionFloor.L3: 3, ActionFloor.L4: 99}

SELECTABLE = [Factor.UEBA, Factor.FACE, Factor.VOICE]

CHALLENGE_BIOMETRICS = [Factor.FACE, Factor.VOICE]


def dynamic_factors(permitted) -> list:
    """The factors that answer a fresh challenge, so a replayed answer proves nothing."""
    own = [f for f in (Factor.OTP, Factor.DEVICE) if f in permitted]
    return own or [f for f in CHALLENGE_BIOMETRICS if f in permitted]


def strength(required, org) -> int:
    """How many factors a set of checks counts as. A fingerprint-unlocked device key is the phone (possession) plus the person
    (inherence), so a bank may count it as two."""
    return sum(2 if (f is Factor.DEVICE and getattr(org, "deviceCountsTwo", False)) else 1 for f in required)


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
    # the same exposure in minor units (this payment plus the last 24 h), for the customer's own limit
    exposureMinor: Optional[int] = None


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
    deviceKey: bool = False         # the phone making this request holds a bound key in its secure hardware


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
    deviceCountsTwo: bool = False   # a device key counts as two factors towards the minimum (the bank's choice)


class Policy(BaseModel):
    actionFloor: ActionFloor = ActionFloor.L2
    maxFactors: int = 3
    guidance: Optional[str] = Field(default=None, max_length=1000)
    smallPaymentCap: bool = True
    # the strongest automated checks replace a person unless a hard-hold sign is present (see adjudicate.py)
    lowTouch: bool = True
    # LEARNING while the model does not yet know this customer: light checks, set by learning_plan; READY once it does
    phase: str = "READY"
    learningDailyMinor: Optional[int] = None
    learningNewPayeeMinor: Optional[int] = None
    # the customer's own no-extra-check limit in force right now, in minor units (None = they set none)
    customerBypassMinor: Optional[int] = None


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
    riskScore: Optional[float] = None
    threatLevel: Optional[str] = None
    modelLevel: Optional[str] = None
    habitLevel: Optional[str] = None       # what the customer's own habit alone asked for
    shadowLevel: Optional[str] = None      # while LEARNING: the level a ready customer would have got (how readiness is judged)
    phase: Optional[str] = None            # LEARNING or READY
    capReason: Optional[str] = None
    riskReasons: list[tuple[str, float]] = Field(default_factory=list)
    modelVersion: str = ""
    modelError: Optional[str] = None
    engineVersion: str = "risk-model-1.0"

    def sequence(self) -> list[Factor]:
        return self.order or [f for f in FRICTION_ORDER if f in self.required]
