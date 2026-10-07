from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

__all__ = ["AmfaClient", "AmfaError", "sign_request", "verify_webhook", "proof_binding"]


class AmfaError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign_request(secret: str, key_id: str, method: str, path_and_query: str, body: bytes) -> dict:
    parts = urlsplit(path_and_query)
    q = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    ts, nonce = str(int(time.time())), secrets.token_urlsafe(18)
    canonical = "\n".join(["AMFA-HMAC-SHA256", method.upper(), parts.path, q, ts, nonce,
                           hashlib.sha256(body).hexdigest()]).encode()
    sig = _b64e(hmac.new(secret.encode(), canonical, hashlib.sha256).digest())
    return {"X-AMFA-Key-Id": key_id, "X-AMFA-Timestamp": ts, "X-AMFA-Nonce": nonce,
            "X-AMFA-Signature": sig}


def verify_webhook(secret: str, body: bytes, timestamp: str, signature: str,
                   tolerance_s: int = 300) -> bool:
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > tolerance_s:
        return False
    expected = "v1=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body,
                                hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def proof_binding(tenant_id: str, customer_ref: str, txn_id: str, ext_txn: str, amount_minor: int,
                  currency: str, payee_ref: str, txn_type: str, iat: int) -> str:
    material = json.dumps([tenant_id, customer_ref, txn_id, ext_txn, int(amount_minor), currency,
                           payee_ref, txn_type, int(iat)], separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


class AmfaClient:
    def __init__(self, base_url: str, key_id: str, secret: str, timeout: float = 15.0,
                 http: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.key_id, self.secret = key_id, secret
        self.http = http or httpx.Client(base_url=self.base_url, timeout=timeout)
        self._jwks: tuple[float, dict] = (0.0, {})

    def request(self, method: str, path: str, body: Any = None, headers: Optional[dict] = None) -> Any:
        raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        h = sign_request(self.secret, self.key_id, method, path, raw)
        h["content-type"] = "application/json"
        h.update(headers or {})
        r = self.http.request(method, path, content=raw, headers=h)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            err = (data or {}).get("error", {})
            raise AmfaError(r.status_code, err.get("code", "ERROR"), err.get("message", r.text[:200]))
        return data

    def create_user(self, external_ref: str, segment: str = "STANDARD", contact: Optional[dict] = None):
        body = {"externalRef": external_ref, "segment": segment}
        if contact:
            body["contact"] = contact
        return self.request("POST", "/v1/users", body)

    def enrollment_link(self, user_id: str, factors: Optional[list[str]] = None, ttl_seconds: int = 3600):
        body: dict = {"ttlSeconds": ttl_seconds}
        if factors:
            body["factors"] = factors
        return self.request("POST", f"/v1/users/{user_id}/enrollment-links", body)

    def erase_user(self, user_id: str):
        return self.request("DELETE", f"/v1/users/{user_id}")

    def initiate(self, *, user_id: str, external_id: str, amount_minor: int, currency: str,
                 payee_ref: str, device_id: str, ip: str, type: str = "TRANSFER",
                 channel: str = "MOBILE", payee_created_at: Optional[float] = None,
                 payee_display: Optional[str] = None, device_first_seen: Optional[float] = None,
                 capability: Optional[dict] = None, session: Optional[dict] = None,
                 idempotency_key: Optional[str] = None):
        payee: dict = {"ref": payee_ref}
        if payee_created_at:
            payee["createdAt"] = payee_created_at
        if payee_display:
            payee["display"] = payee_display
        device: dict = {"deviceId": device_id, "ip": ip}
        if device_first_seen:
            device["firstSeenAt"] = device_first_seen
        body = {"userId": user_id, "externalId": external_id, "type": type, "amountMinor": amount_minor,
                "currency": currency, "payee": payee, "channel": channel, "device": device}
        if capability:
            body["capability"] = capability
        if session:
            body["session"] = session
        return self.request("POST", "/v1/transactions", body,
                            headers={"Idempotency-Key": idempotency_key or f"idem-{uuid.uuid4().hex}"})

    def transaction(self, txn_id: str):
        return self.request("GET", f"/v1/transactions/{txn_id}")

    def fetch_proof(self, txn_id: str):
        return self.request("POST", f"/v1/transactions/{txn_id}/proof", {})

    def verify_proof(self, token: str, *, user_ref: str, external_txn: str, amount_minor: int,
                     currency: str, payee_ref: str, type: str = "TRANSFER", consume: bool = True):
        return self.request("POST", "/v1/proofs/verify", {
            "token": token, "userExternalRef": user_ref, "externalTransactionId": external_txn,
            "amountMinor": amount_minor, "currency": currency, "payeeRef": payee_ref,
            "type": type, "consume": consume})

    def jwks(self) -> dict:
        if self._jwks[0] < time.time():
            r = self.http.get("/.well-known/jwks.json")
            r.raise_for_status()
            self._jwks = (time.time() + 300, r.json())
        return self._jwks[1]

    def verify_proof_offline(self, token: str, *, tenant_id: str, user_ref: str, external_txn: str,
                             amount_minor: int, currency: str, payee_ref: str,
                             type: str = "TRANSFER") -> dict:
        import jwt
        from jwt.algorithms import OKPAlgorithm

        header = jwt.get_unverified_header(token)
        if header.get("alg") != "EdDSA":
            raise AmfaError(401, "ALGORITHM_REJECTED", "proof must be EdDSA-signed")
        jwk = next((k for k in self.jwks()["keys"] if k.get("kid") == header.get("kid")), None)
        if jwk is None:
            raise AmfaError(401, "UNKNOWN_KEY", "proof signed by an unknown key")
        key = OKPAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(token, key, algorithms=["EdDSA"], audience=tenant_id,
                            issuer=self.base_url, options={"require": ["exp", "iat", "jti", "bnd"]})
        if claims["sub"] != user_ref or claims["ext_txn"] != external_txn:
            raise AmfaError(401, "SUBJECT_OR_TRANSACTION_MISMATCH", "proof is for a different transaction")
        expected = proof_binding(tenant_id, user_ref, claims["txn"], external_txn, amount_minor,
                                 currency, payee_ref, type, claims["iat"])
        if not hmac.compare_digest(expected, claims["bnd"]):
            raise AmfaError(401, "BINDING_MISMATCH", "amount or payee differs from what was authorised")
        return claims
