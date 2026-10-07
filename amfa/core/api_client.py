from __future__ import annotations

import time
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from amfa.common import crypto
from amfa.common.web import ApiError, rate_limit
from amfa.core import audit, db, factors, orchestrator, otp, webhooks
from amfa.core.auth import ClientCaller, client_caller
from amfa.core.context_builder import device_key

router = APIRouter(prefix="/c/v1", tags=["customer"])
enroll_caller = client_caller("ENROLL")
session_caller = client_caller("SESSION")

CONSENT_VERSION = "2026-09-v1"
CONSENT_PURPOSES = ["authenticate transactions you initiate", "detect fraud and impersonation"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _load(s, caller: ClientCaller) -> tuple[db.Tenant, db.Customer]:
    tenant = s.get(db.Tenant, caller.tenant_id)
    customer = s.get(db.Customer, caller.customer_id)
    if tenant is None or customer is None or customer.status not in ("ACTIVE",):
        raise ApiError(403, "CUSTOMER_UNAVAILABLE", "this account cannot be used right now")
    return tenant, customer


def _enrolled(s, customer_id: str) -> dict[str, bool]:
    rows = s.execute(select(db.Enrollment).where(db.Enrollment.customer_id == customer_id,
                                                 db.Enrollment.status == "ENROLLED")).scalars().all()
    return {r.factor: True for r in rows}


def _require_consent(customer: db.Customer) -> None:
    if not customer.consent or customer.consent.get("version") != CONSENT_VERSION:
        raise ApiError(403, "CONSENT_REQUIRED", "consent must be given before biometric capture")


def _guard_overwrite(s, caller: ClientCaller, customer: db.Customer, factor: str) -> bool:
    if f"DONE:{factor}" in caller.scope:
        raise ApiError(409, "ALREADY_ENROLLED_WITH_THIS_LINK", f"{factor} was already enrolled with this link")
    existing = s.execute(select(db.Enrollment).where(db.Enrollment.customer_id == customer.id,
                                                     db.Enrollment.factor == factor,
                                                     db.Enrollment.status == "ENROLLED")).scalars().first()
    if existing is not None and not caller.may_replace:
        raise ApiError(409, "ALREADY_ENROLLED", f"{factor} is already enrolled; the bank must issue a "
                       "replacement link to change it")
    return existing is not None


def _mark_done(s, caller: ClientCaller, factor: str, replaced: bool) -> None:
    row = s.get(db.ClientToken, caller.token_id)
    if row is not None:
        row.scope = list(row.scope or []) + [f"DONE:{factor}"]
    if replaced:
        s.add(db.SecurityEvent(tenant_id=caller.tenant_id, customer_id=caller.customer_id, severity="MEDIUM",
                               kind="FACTOR_REPLACED", message=f"{factor} re-enrolled with a replacement link"))
        webhooks.emit(s, caller.tenant_id, "customer.factor_replaced",
                      {"userId": caller.customer_id, "factor": factor})


def _record_enrollment(s, customer: db.Customer, factor: str, detail: dict) -> None:
    row = s.execute(select(db.Enrollment).where(db.Enrollment.customer_id == customer.id,
                                                db.Enrollment.factor == factor)).scalars().first()
    if row is None:
        s.add(db.Enrollment(customer_id=customer.id, factor=factor, detail=detail))
    else:
        row.status, row.detail, row.enrolled_at = "ENROLLED", detail, time.time()


@router.get("/enrollment")
def enrollment_state(caller: ClientCaller = Depends(enroll_caller)):
    with db.session() as s:
        tenant, customer = _load(s, caller)
        done = _enrolled(s, customer.id)
        requested = [f for f in caller.scope if f in ("OTP", "FACE", "VOICE", "UEBA")]
        phrase = factors.ueba_phrase() if "UEBA" in requested else None
        return {"bank": tenant.name, "requested": requested, "replace": caller.may_replace,
                "completedWithThisLink": [x[5:] for x in caller.scope if x.startswith("DONE:")],
                "enrolled": {f: done.get(f, False) for f in requested},
                "consent": {"required": not (customer.consent and customer.consent.get("version") == CONSENT_VERSION),
                            "version": CONSENT_VERSION, "purposes": CONSENT_PURPOSES,
                            "retention": "templates are kept until you or the bank remove them; "
                                         "raw images and recordings are never stored"},
                "otpDelivery": bool(customer.contact_sealed),
                "attestation": {"required": _policy(s, caller.tenant_id).attestation.required,
                                "challenge": crypto.b64e(__import__("amfa.core.attestation", fromlist=["x"])
                                                         .challenge_for("enroll", caller.token_id))},
                "behaviour": phrase}


class Consent(Strict):
    accepted: Literal[True]
    version: str = Field(max_length=32)


@router.post("/enrollment/consent")
def consent(body: Consent, request: Request, caller: ClientCaller = Depends(enroll_caller)):
    if body.version != CONSENT_VERSION:
        raise ApiError(409, "CONSENT_VERSION_STALE", "the notice has changed; reload")
    with db.session() as s:
        _, customer = _load(s, caller)
        customer.consent = {"version": CONSENT_VERSION, "at": int(time.time()), "purposes": CONSENT_PURPOSES,
                            "network": crypto.keyed_hash(request.state.client_ip, "consent-ip", 16)}
        audit.record(f"customer:{customer.id}", "consent.grant", customer.id,
                     {"version": CONSENT_VERSION}, caller.tenant_id, s=s)
    return {"consented": True}


@router.post("/enrollment/totp/start")
def totp_start(caller: ClientCaller = Depends(enroll_caller)):
    if "OTP" not in caller.scope:
        raise ApiError(403, "FACTOR_NOT_REQUESTED", "OTP enrolment was not requested")
    rate_limit(f"enroll:{caller.customer_id}:totp", 10, 600)
    with db.session() as s:
        tenant, customer = _load(s, caller)
        cred = s.get(db.TotpCredential, customer.id)
        if cred is not None and cred.confirmed and not caller.may_replace:
            raise ApiError(409, "ALREADY_ENROLLED", "an authenticator is already enrolled")
        if f"DONE:OTP" in caller.scope:
            raise ApiError(409, "ALREADY_ENROLLED_WITH_THIS_LINK", "OTP was already enrolled with this link")
        secret = otp.new_secret()
        sealed = crypto.seal(secret.encode(), f"totp|{customer.id}", "totp")
        if cred is None:
            s.add(db.TotpCredential(customer_id=customer.id, secret_sealed=sealed))
        else:
            cred.secret_sealed, cred.confirmed = sealed, False
        uri = otp.provisioning_uri(secret, customer.external_ref[-12:], tenant.name)
    return {"otpauthUri": uri, "qrSvg": otp.qr_svg(uri), "secret": secret}


class Code(Strict):
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/enrollment/totp/confirm")
def totp_confirm(body: Code, caller: ClientCaller = Depends(enroll_caller)):
    rate_limit(f"enroll:{caller.customer_id}:totp-confirm", 10, 600)
    with db.session() as s:
        _, customer = _load(s, caller)
        cred = s.get(db.TotpCredential, customer.id)
        if cred is None:
            raise ApiError(409, "TOTP_NOT_STARTED", "start authenticator enrolment first")
        secret = crypto.open_(cred.secret_sealed, f"totp|{customer.id}", "totp").decode()
        step = otp.verify_totp(secret, body.code, cred.last_step)
        if step is None:
            raise ApiError(400, "OTP_INVALID", "that code is not valid")
        replaced = cred.confirmed
        cred.confirmed, cred.last_step = True, step
        _record_enrollment(s, customer, "OTP", {"method": "totp"})
        _mark_done(s, caller, "OTP", replaced)
    return {"enrolled": True, "factor": "OTP"}


class FaceEnroll(Strict):
    frames: list[str] = Field(min_length=3, max_length=8)


class VoiceEnroll(Strict):
    samples: list[str] = Field(min_length=3, max_length=5)


class BehaviourEnroll(Strict):
    keystrokeSamples: list[list[dict]] = Field(min_length=1, max_length=20)
    shapeTraces: list[list[dict]] = Field(default_factory=list, max_length=10)
    mouseSegments: list[list[dict]] = Field(default_factory=list, max_length=20)
    mouseClicks: list[dict] = Field(default_factory=list, max_length=500)


def _biometric_enroll(caller: ClientCaller, factor: str, payload: dict, rate_key: str) -> dict:
    if factor not in caller.scope:
        raise ApiError(403, "FACTOR_NOT_REQUESTED", f"{factor} enrolment was not requested")
    rate_limit(f"enroll:{caller.customer_id}:{rate_key}", 8, 600)
    with db.session() as s:
        _, customer = _load(s, caller)
        _require_consent(customer)
        replaced = _guard_overwrite(s, caller, customer, factor)
    res = factors.enroll(factor, tenant=caller.tenant_id, user=caller.customer_id, payload=payload)
    if not res.enrolled:
        return {"enrolled": False, "factor": factor, "reason": res.reason, "quality": res.quality}
    detail = {"model": res.modelVersion}
    if factor == "UEBA":
        detail["shape"] = (res.quality.get("shape") or {}).get("status") == "ENROLLED"
    with db.session() as s:
        _, customer = _load(s, caller)
        _record_enrollment(s, customer, factor, detail)
        _mark_done(s, caller, factor, replaced)
        audit.record(f"customer:{customer.id}", "enrollment.factor", customer.id,
                     {"factor": factor, "replaced": replaced}, caller.tenant_id, s=s)
    return {"enrolled": True, "factor": factor, "quality": res.quality}


@router.post("/enrollment/face")
def enroll_face(body: FaceEnroll, caller: ClientCaller = Depends(enroll_caller)):
    return _biometric_enroll(caller, "FACE", body.model_dump(), "face")


@router.post("/enrollment/voice")
def enroll_voice(body: VoiceEnroll, caller: ClientCaller = Depends(enroll_caller)):
    return _biometric_enroll(caller, "VOICE", body.model_dump(), "voice")


@router.post("/enrollment/behaviour")
def enroll_behaviour(body: BehaviourEnroll, caller: ClientCaller = Depends(enroll_caller)):
    return _biometric_enroll(caller, "UEBA", body.model_dump(), "behaviour")


class Attestation(Strict):
    platform: Literal["ios", "android"]
    keyId: Optional[str] = Field(default=None, max_length=100)
    object: Optional[str] = Field(default=None, max_length=16_000)
    integrityToken: Optional[str] = Field(default=None, max_length=16_000)


class DeviceKey(Strict):
    deviceId: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    publicJwk: Optional[dict] = None
    attestation: Optional[Attestation] = None


def _policy(s, tenant_id: str):
    from amfa.engine.policy import TenantPolicy
    return TenantPolicy.model_validate(s.get(db.Tenant, tenant_id).policy)


def _check_attestation(att: Optional[Attestation], challenge: bytes, pol, *, jwk: Optional[dict]):
    from amfa.core import attestation as A
    ap = pol.attestation
    if att is None:
        if ap.required:
            raise ApiError(403, "ATTESTATION_REQUIRED", "this bank requires app attestation")
        if jwk is None:
            raise ApiError(422, "DEVICE_KEY_INVALID", "a public key is required")
        return "web", _valid_p256_jwk(jwk), 0
    try:
        if att.platform == "ios":
            if not (ap.iosAppId and att.keyId and att.object):
                raise ApiError(422, "ATTESTATION_INCOMPLETE", "iOS attestation needs keyId and object")
            k = A.verify_app_attest(att.object, att.keyId, challenge, ap.iosAppId,
                                    allow_development=ap.allowDevelopment)
            return "ios", k.public_jwk, 0
        if not (ap.androidPackage and att.integrityToken and jwk):
            raise ApiError(422, "ATTESTATION_INCOMPLETE", "Android needs integrityToken and the Keystore publicJwk")
        A.verify_play_integrity(att.integrityToken, nonce=crypto.b64e(challenge), package=ap.androidPackage)
        return "android", _valid_p256_jwk(jwk), 0
    except A.AttestationError as exc:
        raise ApiError(403, f"ATTESTATION_{exc.code}", "app attestation failed") from None


def _valid_p256_jwk(jwk: dict) -> dict:
    from cryptography.hazmat.primitives.asymmetric import ec
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or "d" in jwk:
        raise ApiError(422, "DEVICE_KEY_INVALID", "an EC P-256 public key is required")
    try:
        ec.EllipticCurvePublicNumbers(int.from_bytes(crypto.b64d(jwk["x"]), "big"),
                                      int.from_bytes(crypto.b64d(jwk["y"]), "big"),
                                      ec.SECP256R1()).public_key()
    except (KeyError, ValueError):
        raise ApiError(422, "DEVICE_KEY_INVALID", "not a valid P-256 point") from None
    return {"kty": "EC", "crv": "P-256", "x": jwk["x"], "y": jwk["y"]}


@router.post("/enrollment/device")
def enroll_device(body: DeviceKey, caller: ClientCaller = Depends(enroll_caller)):
    from amfa.core.attestation import challenge_for
    with db.session() as s:
        pol = _policy(s, caller.tenant_id)
    platform, jwk, counter = _check_attestation(body.attestation, challenge_for("enroll", caller.token_id),
                                                pol, jwk=body.publicJwk)
    with db.session() as s:
        _, customer = _load(s, caller)
        key = device_key(caller.tenant_id, body.deviceId)
        dev = s.execute(select(db.Device).where(db.Device.customer_id == customer.id,
                                                db.Device.device_key == key)).scalars().first()
        others = s.execute(select(db.Device).where(db.Device.customer_id == customer.id,
                                                   db.Device.device_key != key,
                                                   db.Device.public_jwk.is_not(None))).scalars().first()
        if others is not None and not caller.may_replace:
            raise ApiError(409, "DEVICE_BINDING_REQUIRES_REPLACE",
                           "this customer already has a bound device; the bank must issue a replacement link")
        if dev is None:
            s.add(db.Device(customer_id=customer.id, device_key=key, public_jwk=jwk, verified_count=1,
                            platform=platform, sign_count=counter))
        elif dev.public_jwk and dev.public_jwk != jwk:
            raise ApiError(409, "DEVICE_ALREADY_BOUND", "this device is bound to a different key")
        else:
            dev.public_jwk, dev.platform, dev.sign_count = jwk, platform, counter
            dev.verified_count = max(dev.verified_count, 1)
        _mark_done(s, caller, "DEVICE", others is not None)
        audit.record(f"customer:{customer.id}", "device.bind", customer.id,
                     {"additional": others is not None}, caller.tenant_id, s=s)
    return {"bound": True}


@router.post("/enrollment/complete")
def enrollment_complete(request: Request, caller: ClientCaller = Depends(enroll_caller)):
    with db.session() as s:
        _, customer = _load(s, caller)
        done = _enrolled(s, customer.id)
        auth = request.headers.get("authorization", "")[7:].strip()
        row = s.get(db.ClientToken, crypto.hash_token(auth))
        if row:
            row.revoked = True
        webhooks.emit(s, caller.tenant_id, "customer.enrolled",
                      {"userId": customer.id, "externalRef": customer.external_ref,
                       "factors": sorted(done)})
    return {"complete": True, "enrolled": sorted(done)}


def _session(s, caller: ClientCaller):
    sess = s.get(db.AuthSession, caller.session_id)
    if sess is None or sess.customer_id != caller.customer_id:
        raise ApiError(404, "SESSION_NOT_FOUND", "no such session")
    txn = s.get(db.Transaction, sess.transaction_id)
    tenant = s.get(db.Tenant, sess.tenant_id)
    customer = s.get(db.Customer, sess.customer_id)
    if sess.status == orchestrator.PENDING and sess.expires_at < time.time():
        orchestrator._expire(s, sess, txn)
    return sess, txn, tenant, customer


def _state(s, sess, txn, tenant, customer) -> dict:
    challenge = None
    if sess.status == orchestrator.PENDING and customer.status == "ACTIVE":
        challenge = orchestrator.issue_challenge(s, sess, customer, txn, tenant)
    return {
        "sessionId": sess.id,
        "status": sess.status,
        "approving": {"bank": tenant.name, "type": txn.type,
                      "amount": orchestrator.format_amount(txn.amount_minor, txn.currency),
                      "payee": txn.payee_display or orchestrator.mask_ref(txn.payee_ref)},
        "steps": [{"factor": i["factor"], "status": i["status"]} for i in sess.checklist],
        "challenge": challenge,
        "deviceNonce": orchestrator.device_nonce(sess.id),
        "deviceBound": sess.device_bound,
        "expiresAt": int(sess.expires_at),
    }


@router.get("/session")
def session_state(caller: ClientCaller = Depends(session_caller)):
    with db.session() as s:
        return _state(s, *_session(s, caller))


class DeviceProof(Strict):
    deviceId: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    signature: Optional[str] = Field(default=None, max_length=200)
    assertion: Optional[str] = Field(default=None, max_length=8000)
    integrityToken: Optional[str] = Field(default=None, max_length=16_000)


@router.post("/session/device")
def session_device(body: DeviceProof, caller: ClientCaller = Depends(session_caller)):
    rate_limit(f"sess:{caller.session_id}:device", 5, 300)
    with db.session() as s:
        sess, txn, tenant, customer = _session(s, caller)
        key = device_key(tenant.id, body.deviceId)
        ok = key == (txn.facts or {}).get("deviceKey") and \
            orchestrator.bind_device(s, sess, customer, key, signature=body.signature,
                                     assertion=body.assertion, integrity_token=body.integrityToken,
                                     policy=_policy(s, tenant.id))
    return {"bound": ok}


class FactorSubmission(Strict):
    challengeId: str = Field(pattern=r"^ch_[a-f0-9]{20}$")
    payload: dict[str, Any]


@router.post("/session/factors/{factor}")
def submit_factor(factor: Literal["OTP", "FACE", "VOICE", "UEBA"], body: FactorSubmission,
                  caller: ClientCaller = Depends(session_caller)):
    rate_limit(f"sess:{caller.session_id}:submit", 30, 600)
    with db.session() as s:
        sess, txn, tenant, customer = _session(s, caller)
        if customer.status != "ACTIVE":
            raise ApiError(403, "CUSTOMER_UNAVAILABLE", "this account cannot be used right now")
        outcome = orchestrator.submit(s, sess=sess, customer=customer, txn=txn, tenant=tenant,
                                      factor=factor, challenge_id=body.challengeId, payload=body.payload)
        s.flush()
        return {**outcome, "state": _state(s, sess, txn, tenant, customer)}
