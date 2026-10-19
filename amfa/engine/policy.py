from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .context import (ActionFloor, Appetite, Band, Factor, OrgCtx, Regulatory, Segment, TxnType,
                      max_floor)


@dataclass(frozen=True)
class RegulatoryProfile:
    code: Regulatory
    min_floor: ActionFloor
    exemption_allowed: bool
    exemption_types: frozenset[TxnType]
    exemption_cap_minor: Optional[dict[str, int]]
    note: str


REGULATORY = {
    Regulatory.RBI_2025: RegulatoryProfile(
        Regulatory.RBI_2025, ActionFloor.L2, True,
        frozenset({TxnType.TRANSFER, TxnType.PAYMENT, TxnType.CARD_NOT_PRESENT}), None,
        "additional factor of authentication; at least two factors, one dynamic; risk-based "
        "exemption for low-value payments at the bank's configured ceiling"),
    Regulatory.PSD2_SCA: RegulatoryProfile(
        Regulatory.PSD2_SCA, ActionFloor.L2, True,
        frozenset({TxnType.PAYMENT, TxnType.CARD_NOT_PRESENT}), {"EUR": 3000},
        "strong customer authentication; low-value remote payment exemption"),
    Regulatory.BASELINE: RegulatoryProfile(
        Regulatory.BASELINE, ActionFloor.L1, True, frozenset(TxnType), None,
        "no regulatory minimum beyond OTP; for non-payment use only"),
}


def platform_exemption_caps() -> dict[str, int]:
    import os
    raw = os.getenv("AMFA_EXEMPTION_CAPS", "INR:500000,EUR:3000,USD:5000,GBP:3000")
    out: dict[str, int] = {}
    for part in raw.split(","):
        if ":" in part:
            cur, amount = part.split(":", 1)
            out[cur.strip().upper()] = int(amount)
    return out


class AmountBands(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mediumFromMinor: int = Field(ge=1)
    highFromMinor: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.highFromMinor <= self.mediumFromMinor:
            raise ValueError("highFromMinor must exceed mediumFromMinor")
        return self


class Exemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    maxAmountMinor: int = Field(default=0, ge=0)
    types: list[TxnType] = Field(default_factory=lambda: [TxnType.TRANSFER, TxnType.PAYMENT])


class TxnRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: TxnType
    floor: Optional[ActionFloor] = None
    highValueFloor: Optional[ActionFloor] = None
    requireFactors: list[Factor] = Field(default_factory=list, max_length=3)
    # never hold this type for a person: the customer does every check they can instead
    neverReview: bool = False


class SegmentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment: Segment
    floor: Optional[ActionFloor] = None
    requireFactors: list[Factor] = Field(default_factory=list, max_length=3)


class AgentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    maxAdditions: int = Field(default=3, ge=0, le=3)
    guidance: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("guidance")
    @classmethod
    def printable(cls, v):
        if v is None:
            return v
        v = " ".join(v.split())
        if any(ord(ch) < 32 for ch in v):
            raise ValueError("guidance may not contain control characters")
        return v or None


class AttestationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: bool = False
    iosAppId: Optional[str] = Field(default=None, pattern=r"^[A-Z0-9]{10}\.[A-Za-z0-9.-]{1,155}$")
    androidPackage: Optional[str] = Field(default=None, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
    allowDevelopment: bool = False


class Branding(BaseModel):
    """How the hosted setup and verification pages look for this bank. The colour is written into
    their CSS, so only a plain #RRGGBB is accepted."""
    model_config = ConfigDict(extra="forbid")
    color: str = Field(default="#1B4FD8", pattern=r"^#[0-9A-Fa-f]{6}$")


class QuietHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(default=23, ge=0, le=23)
    end: int = Field(default=5, ge=0, le=23)

    def contains(self, hour: int) -> bool:
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= hour < self.end
        return hour >= self.start or hour < self.end


class TenantPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regulatoryProfile: Regulatory = Regulatory.RBI_2025
    riskAppetite: Appetite = Appetite.BALANCED
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    amountBands: dict[str, AmountBands] = Field(default_factory=lambda: {
        "INR": AmountBands(mediumFromMinor=10_000_00, highFromMinor=1_00_000_00)})
    lowValueExemption: Exemption = Field(default_factory=Exemption)
    defaultFloor: ActionFloor = ActionFloor.L2
    transactionRules: list[TxnRule] = Field(default_factory=lambda: [
        TxnRule(type=TxnType.TRANSFER, highValueFloor=ActionFloor.L3),
        # saving a payee moves no money; the first payment to it is risk-checked as a new payee
        TxnRule(type=TxnType.ADD_BENEFICIARY, floor=ActionFloor.L2, requireFactors=[Factor.FACE], neverReview=True),
        TxnRule(type=TxnType.CHANGE_LIMIT, floor=ActionFloor.L3),
        TxnRule(type=TxnType.PROFILE_CHANGE, floor=ActionFloor.L3),
        TxnRule(type=TxnType.DEVICE_BINDING, floor=ActionFloor.L3, requireFactors=[Factor.FACE]),
    ], max_length=20)
    segmentRules: list[SegmentRule] = Field(default_factory=lambda: [
        SegmentRule(segment=Segment.PREMIUM, floor=ActionFloor.L3),
        SegmentRule(segment=Segment.WATCHLIST, floor=ActionFloor.L4),
    ], max_length=20)
    permittedFactors: list[Factor] = Field(
        default_factory=lambda: [Factor.OTP, Factor.UEBA, Factor.FACE, Factor.VOICE])
    padModes: dict[str, str] = Field(default_factory=lambda: {"FACE": "advisory", "VOICE": "advisory"})
    attemptBudget: dict[str, int] = Field(default_factory=lambda: {
        "OTP": 3, "UEBA": 2, "FACE": 2, "VOICE": 2})
    sessionTtlSeconds: int = Field(default=300, ge=60, le=900)
    proofTtlSeconds: int = Field(default=120, ge=30, le=600)
    newPayeeDays: int = Field(default=30, ge=0, le=365)
    # strongest automated checks instead of a person, except for hard-hold signs; False restores "high risk -> review"
    lowTouch: bool = True
    deviceTrustDays: int = Field(default=0, ge=0, le=90)
    dormancyDays: int = Field(default=180, ge=30, le=1095)
    quietHours: QuietHours = Field(default_factory=QuietHours)
    coldStartBehaviouralBand: Band = Band.HIGH
    reviewOnAnyUnmet: bool = False
    agent: AgentPolicy = Field(default_factory=AgentPolicy)
    allowedEmbedOrigins: list[str] = Field(default_factory=list, max_length=10)
    attestation: AttestationPolicy = Field(default_factory=AttestationPolicy)
    branding: Branding = Field(default_factory=Branding)

    @field_validator("permittedFactors")
    @classmethod
    def dynamic_required(cls, v):
        v = list(dict.fromkeys(v))
        if Factor.OTP not in v and not (Factor.FACE in v or Factor.VOICE in v):
            raise ValueError("without OTP, FACE or VOICE must be permitted as the dynamic factor")
        return v

    @field_validator("padModes")
    @classmethod
    def pad_modes(cls, v):
        for k, mode in v.items():
            if k not in ("FACE", "VOICE") or mode not in ("advisory", "terminal"):
                raise ValueError("padModes maps FACE/VOICE to advisory|terminal")
        return v

    @field_validator("attemptBudget")
    @classmethod
    def budgets(cls, v):
        for k, n in v.items():
            if k not in Factor.__members__ or not 1 <= n <= 5:
                raise ValueError("attemptBudget maps a factor to 1..5 attempts")
        return v

    @field_validator("coldStartBehaviouralBand")
    @classmethod
    def never_low(cls, v):
        if v is Band.LOW:
            raise ValueError("coldStartBehaviouralBand may be MEDIUM or HIGH, never LOW")
        return v

    @field_validator("allowedEmbedOrigins")
    @classmethod
    def origins(cls, v):
        import re
        from amfa import config
        local_ok = not config.get().production
        for o in v:
            if re.fullmatch(r"https://[a-z0-9.-]+(:\d{1,5})?", o):
                continue
            if local_ok and re.fullmatch(r"http://(localhost|127\.0\.0\.1)(:\d{1,5})?", o):
                continue
            raise ValueError(f"embed origin must be https://host[:port], got {o!r}")
        return v

    @model_validator(mode="after")
    def exemption_within_profile(self):
        prof = REGULATORY[self.regulatoryProfile]
        ex = self.lowValueExemption
        if ex.enabled:
            if not prof.exemption_allowed:
                raise ValueError(f"{prof.code.value} does not permit a low-value exemption")
            bad = [t.value for t in ex.types if t not in prof.exemption_types]
            if bad:
                raise ValueError(f"{prof.code.value} does not permit exemption for {bad}")
            cap = (prof.exemption_cap_minor or {}).get(self.currency)
            if cap is not None and ex.maxAmountMinor > cap:
                raise ValueError(f"exemption ceiling exceeds the {prof.code.value} cap of {cap}")
            plat = platform_exemption_caps().get(self.currency)
            if plat is None:
                raise ValueError(f"no platform exemption ceiling is configured for {self.currency}")
            if ex.maxAmountMinor > plat:
                raise ValueError(f"exemption ceiling exceeds the platform ceiling of {plat} minor units")
        if self.currency not in self.amountBands:
            raise ValueError(f"amountBands has no entry for {self.currency}")
        return self


    def band_for(self, amount_minor: int, currency: str) -> Band:
        bands = self.amountBands.get(currency)
        if bands is None:
            return Band.HIGH
        if amount_minor >= bands.highFromMinor:
            return Band.HIGH
        if amount_minor >= bands.mediumFromMinor:
            return Band.MEDIUM
        return Band.LOW

    def derive(self, txn_type: TxnType, band: Band, amount_minor: int, currency: str,
               segment: Segment, is_new_payee: bool) -> tuple[ActionFloor, OrgCtx]:
        prof = REGULATORY[self.regulatoryProfile]
        ex = self.lowValueExemption
        exempt = (ex.enabled and prof.exemption_allowed and txn_type in ex.types
                  and txn_type in prof.exemption_types and currency == self.currency
                  and amount_minor < ex.maxAmountMinor and not is_new_payee
                  and segment in (Segment.STANDARD, Segment.CORPORATE))
        base = ActionFloor.L1 if exempt else max_floor(prof.min_floor, self.defaultFloor)

        required: list[Factor] = []
        floors = [base]
        never_review = False
        for r in self.transactionRules:
            if r.type is txn_type:
                never_review = never_review or r.neverReview
                floors.append(r.floor)
                if band is Band.HIGH:
                    floors.append(r.highValueFloor)
                required += r.requireFactors
        for r in self.segmentRules:
            if r.segment is segment:
                floors.append(r.floor)
                required += r.requireFactors

        floor = max_floor(*floors)
        permitted = list(self.permittedFactors)
        org = OrgCtx(regulatory=self.regulatoryProfile, appetite=self.riskAppetite,
                     segment=segment, permitted=permitted,
                     required=[f for f in dict.fromkeys(required) if f in permitted],
                     agentMaxAdditions=self.agent.maxAdditions if self.agent.enabled else 0,
                     reviewOnAnyUnmet=self.reviewOnAnyUnmet, lowValueExempt=exempt and floor is ActionFloor.L1,
                     neverReview=never_review)
        return floor, org
