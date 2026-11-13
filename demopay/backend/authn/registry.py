"""
The authenticators as data. A bank's risk team can edit this file (or load it from a config store) without touching code.

ALL pass rates, friction costs and seconds below are ASSUMPTIONS (see docs/authenticators-explained.pdf), not
measurements. Replace them with logged outcomes (AuthChallenge) once real traffic exists.
pass_by_family = chance the check passes if the payment is a scam of that family:
  UNAUTHORISED  someone else is paying (takeover)
  COACHED       the real owner is being tricked
  PAYEE_RISK    the receiving account is the problem
"""
from dataclasses import dataclass, field
from typing import Dict

FAMILIES = ("UNAUTHORISED", "COACHED", "PAYEE_RISK")


@dataclass(frozen=True)
class Method:
    id: str
    name: str
    tier: str                     # "fast" or "slow" (slow steps are rare escalation only)
    channel: str                  # device | other_device | human | bank | time | education | payee
    answers: str                  # which doubt it reduces: identity | intent | payee
    seconds: float                # user time
    cost: float                   # friction, in rupee-equivalents
    pass_genuine: float
    pass_by_family: Dict[str, float] = field(default_factory=dict)
    implemented: bool = False
    universal: bool = True        # True: no enrolment needed


METHODS: Dict[str, Method] = {m.id: m for m in [
    # ---- built in this app ----
    Method("warning", "Scam warning and questions", "fast", "education", "intent", 8, 2, 1.0,
           {"UNAUTHORISED": 1.0, "COACHED": 0.6, "PAYEE_RISK": 0.9}, implemented=True),
    Method("typed_confirm", "Type the payee's registered name", "fast", "payee", "payee", 8, 3, 0.98,
           {"UNAUTHORISED": 0.98, "COACHED": 0.85, "PAYEE_RISK": 0.5}, implemented=True),
    Method("payee_check", "Payee check", "fast", "payee", "payee", 6, 4, 0.97,
           {"UNAUTHORISED": 0.9, "COACHED": 0.9, "PAYEE_RISK": 0.3}, implemented=True),
    Method("cooling_off", "Cooling-off wait", "slow", "time", "intent", 1800, 25, 0.90,
           {"UNAUTHORISED": 0.4, "COACHED": 0.35, "PAYEE_RISK": 0.5}, implemented=True),
    # ---- planned (listed so the Security page can show them; the engine ignores them until implemented) ----
    Method("second_device", "Second-device approval", "fast", "other_device", "identity", 15, 6, 0.95,
           {"UNAUTHORISED": 0.03, "COACHED": 1.0, "PAYEE_RISK": 1.0}, universal=False),
    Method("face", "Face + liveness", "fast", "device", "identity", 20, 8, 0.873,
           {"UNAUTHORISED": 0.087, "COACHED": 1.0, "PAYEE_RISK": 1.0}, universal=False),
    Method("device_key", "Device key with payment signing", "fast", "device", "identity", 1, 0.5, 0.98,
           {"UNAUTHORISED": 0.05, "COACHED": 1.0, "PAYEE_RISK": 1.0}, universal=False),
    Method("totp", "Authenticator-app code", "fast", "other_device", "identity", 10, 3, 0.96,
           {"UNAUTHORISED": 0.10, "COACHED": 1.0, "PAYEE_RISK": 1.0}, universal=False),
    Method("passkey", "Passkey", "fast", "device", "identity", 4, 2, 0.98,
           {"UNAUTHORISED": 0.05, "COACHED": 1.0, "PAYEE_RISK": 1.0}, universal=False),
    Method("trusted_contact", "Trusted-contact approval", "slow", "human", "intent", 180, 40, 0.85,
           {"UNAUTHORISED": 0.05, "COACHED": 0.25, "PAYEE_RISK": 0.6}, universal=False),
    Method("call_back", "Bank call-back", "slow", "bank", "identity", 300, 60, 0.90,
           {"UNAUTHORISED": 0.02, "COACHED": 0.30, "PAYEE_RISK": 0.4}, universal=False),
]}

UNIVERSAL_IMPLEMENTED = [m.id for m in METHODS.values() if m.implemented and m.universal]
