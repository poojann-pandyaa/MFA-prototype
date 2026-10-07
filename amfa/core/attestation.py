from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOTS = Path(__file__).resolve().parent / "attestation_roots"
APPLE_ROOT_SHA256 = "1cb9823ba28ba6ad2d33a006941de2ae4f513ef1d4e831b9f7e0fa7b6242c932"
APPLE_NONCE_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.2")
AAGUID_PROD = b"appattest" + b"\x00" * 7
AAGUID_DEV = b"appattestdevelop"


class AttestationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _b64d(s: str) -> bytes:
    s = s.strip()
    return base64.b64decode(s + "=" * (-len(s) % 4), altchars=b"-_" if ("-" in s or "_" in s) else None)


def apple_root() -> x509.Certificate:
    pem = (ROOTS / "Apple_App_Attestation_Root_CA.pem").read_bytes()
    cert = x509.load_pem_x509_certificate(pem)
    if cert.fingerprint(hashes.SHA256()).hex() != APPLE_ROOT_SHA256:
        raise AttestationError("APPLE_ROOT_TAMPERED")
    return cert


@dataclass
class AttestedKey:
    platform: str
    key_id: str
    public_jwk: dict
    counter: int
    environment: str


def _parse_auth_data(auth: bytes) -> dict:
    if len(auth) < 37:
        raise AttestationError("AUTHDATA_SHORT")
    out = {"rpIdHash": auth[:32], "flags": auth[32], "counter": int.from_bytes(auth[33:37], "big")}
    if len(auth) >= 55:
        out["aaguid"] = auth[37:53]
        n = int.from_bytes(auth[53:55], "big")
        out["credentialId"] = auth[55:55 + n]
    return out


def _verify_chain(leaf: x509.Certificate, chain: list[x509.Certificate], root: x509.Certificate) -> None:
    certs = [leaf, *chain]
    now = time.time()
    for child, parent in zip(certs, certs[1:] + [root]):
        if not (child.not_valid_before_utc.timestamp() <= now <= child.not_valid_after_utc.timestamp()):
            raise AttestationError("CERT_EXPIRED")
        if child.issuer != parent.subject:
            raise AttestationError("CHAIN_BROKEN")
        try:
            child.verify_directly_issued_by(parent)
        except (ValueError, TypeError, InvalidSignature):
            raise AttestationError("CHAIN_SIGNATURE_INVALID") from None


def _nonce_from_extension(cert: x509.Certificate) -> bytes:
    try:
        ext = cert.extensions.get_extension_for_oid(APPLE_NONCE_OID).value.value
    except x509.ExtensionNotFound:
        raise AttestationError("NONCE_EXTENSION_MISSING") from None
    if len(ext) < 32 or ext[-34] != 0x04 or ext[-33] != 32:
        raise AttestationError("NONCE_EXTENSION_MALFORMED")
    return ext[-32:]


def _jwk(pub: ec.EllipticCurvePublicKey) -> dict:
    n = pub.public_numbers()
    e = lambda i: base64.urlsafe_b64encode(i.to_bytes(32, "big")).decode().rstrip("=")
    return {"kty": "EC", "crv": "P-256", "x": e(n.x), "y": e(n.y)}


def verify_app_attest(attestation_b64: str, key_id_b64: str, challenge: bytes, app_id: str,
                      root: Optional[x509.Certificate] = None, allow_development: bool = False) -> AttestedKey:
    import cbor2
    try:
        obj = cbor2.loads(_b64d(attestation_b64))
        stmt, auth = obj["attStmt"], obj["authData"]
        x5c = [x509.load_der_x509_certificate(c) for c in stmt["x5c"]]
    except Exception:
        raise AttestationError("ATTESTATION_MALFORMED") from None
    if obj.get("fmt") != "apple-appattest" or len(x5c) < 2:
        raise AttestationError("ATTESTATION_FORMAT")
    leaf = x5c[0]
    _verify_chain(leaf, x5c[1:], root or apple_root())

    client_hash = hashlib.sha256(challenge).digest()
    if _nonce_from_extension(leaf) != hashlib.sha256(auth + client_hash).digest():
        raise AttestationError("NONCE_MISMATCH")
    pub = leaf.public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        raise AttestationError("KEY_TYPE")
    raw = pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    key_id = _b64d(key_id_b64)
    if hashlib.sha256(raw).digest() != key_id:
        raise AttestationError("KEY_ID_MISMATCH")
    ad = _parse_auth_data(auth)
    if ad["rpIdHash"] != hashlib.sha256(app_id.encode()).digest():
        raise AttestationError("APP_ID_MISMATCH")
    if ad["counter"] != 0:
        raise AttestationError("COUNTER_NOT_ZERO")
    env = {AAGUID_PROD: "production", AAGUID_DEV: "development"}.get(ad.get("aaguid", b""))
    if env is None or (env == "development" and not allow_development):
        raise AttestationError("AAGUID_NOT_PRODUCTION")
    if ad.get("credentialId") != key_id:
        raise AttestationError("CREDENTIAL_ID_MISMATCH")
    return AttestedKey("ios", key_id_b64, _jwk(pub), 0, env)


def verify_app_assertion(assertion_b64: str, challenge: bytes, app_id: str, public_jwk: dict,
                         last_counter: int) -> int:
    import cbor2
    try:
        obj = cbor2.loads(_b64d(assertion_b64))
        sig, auth = obj["signature"], obj["authenticatorData"]
    except Exception:
        raise AttestationError("ASSERTION_MALFORMED") from None
    pub = ec.EllipticCurvePublicNumbers(int.from_bytes(_b64d(public_jwk["x"]), "big"),
                                        int.from_bytes(_b64d(public_jwk["y"]), "big"), ec.SECP256R1()).public_key()
    nonce = hashlib.sha256(auth + hashlib.sha256(challenge).digest()).digest()
    try:
        pub.verify(sig, nonce, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise AttestationError("ASSERTION_SIGNATURE_INVALID") from None
    ad = _parse_auth_data(auth)
    if ad["rpIdHash"] != hashlib.sha256(app_id.encode()).digest():
        raise AttestationError("APP_ID_MISMATCH")
    if ad["counter"] <= last_counter:
        raise AttestationError("COUNTER_NOT_INCREASING")
    return ad["counter"]


_token_cache: dict[str, tuple[float, str]] = {}


def _google_access_token(http: httpx.Client) -> str:
    import jwt
    path = os.getenv("AMFA_PLAY_INTEGRITY_SA_JSON")
    if not path:
        raise AttestationError("PLAY_INTEGRITY_NOT_CONFIGURED")
    hit = _token_cache.get(path)
    if hit and hit[0] > time.time() + 60:
        return hit[1]
    sa = json.loads(Path(path).read_text())
    now = int(time.time())
    assertion = jwt.encode({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/playintegrity",
                            "aud": sa["token_uri"], "iat": now, "exp": now + 600},
                           sa["private_key"], algorithm="RS256")
    r = http.post(sa["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                         "assertion": assertion}, timeout=5.0)
    if r.status_code != 200:
        raise AttestationError("PLAY_INTEGRITY_AUTH_FAILED")
    tok = r.json()
    _token_cache[path] = (time.time() + int(tok.get("expires_in", 3600)), tok["access_token"])
    return tok["access_token"]


def evaluate_play_verdict(payload: dict, *, nonce: str, package: str, max_age_s: int = 120) -> None:
    req = payload.get("requestDetails", {})
    if req.get("requestPackageName") != package:
        raise AttestationError("PACKAGE_MISMATCH")
    if req.get("nonce") != nonce and req.get("requestHash") != nonce:
        raise AttestationError("NONCE_MISMATCH")
    ts = int(req.get("timestampMillis", 0)) / 1000
    if abs(time.time() - ts) > max_age_s:
        raise AttestationError("VERDICT_STALE")
    app = payload.get("appIntegrity", {})
    if app.get("appRecognitionVerdict") != "PLAY_RECOGNIZED" or app.get("packageName") != package:
        raise AttestationError("APP_NOT_RECOGNIZED")
    if "MEETS_DEVICE_INTEGRITY" not in payload.get("deviceIntegrity", {}).get("deviceRecognitionVerdict", []):
        raise AttestationError("DEVICE_INTEGRITY_FAILED")


def verify_play_integrity(token: str, *, nonce: str, package: str, http: Optional[httpx.Client] = None) -> dict:
    http = http or httpx.Client()
    access = _google_access_token(http)
    r = http.post(f"https://playintegrity.googleapis.com/v1/{package}:decodeIntegrityToken",
                  json={"integrity_token": token}, headers={"authorization": f"Bearer {access}"}, timeout=5.0)
    if r.status_code != 200:
        raise AttestationError("PLAY_INTEGRITY_DECODE_FAILED")
    payload = r.json().get("tokenPayloadExternal", {})
    evaluate_play_verdict(payload, nonce=nonce, package=package)
    return payload


def challenge_for(purpose: str, subject: str) -> bytes:
    from amfa.common import crypto
    import hmac
    return hmac.new(crypto.derive_key("attestation-challenge"), f"{purpose}|{subject}".encode(),
                    hashlib.sha256).digest()
