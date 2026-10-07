from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select, update

from amfa import config
from amfa.common import crypto
from amfa.core import audit, db

ALG = "EdDSA"
_CACHE: dict[str, object] = {}
_LOCK = threading.Lock()


def binding(tenant_id: str, customer_ref: str, txn_id: str, ext_txn: str, amount_minor: int,
            currency: str, payee_ref: str, txn_type: str, iat: int) -> str:
    material = json.dumps([tenant_id, customer_ref, txn_id, ext_txn, int(amount_minor),
                           currency, payee_ref, txn_type, int(iat)], separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def _jwk(kid: str, pub: Ed25519PublicKey) -> dict:
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "x": crypto.b64e(raw), "kid": kid,
            "use": "sig", "alg": ALG}


def rotate_key(actor: str = "system") -> str:
    key = Ed25519PrivateKey.generate()
    kid = f"k_{uuid.uuid4().hex[:12]}"
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    with db.session() as s:
        for old in s.execute(select(db.SigningKey).where(db.SigningKey.status == "ACTIVE")).scalars():
            old.status, old.retired_at = "RETIRED", time.time()
        s.add(db.SigningKey(kid=kid, private_sealed=crypto.seal(pem, f"signing|{kid}", "signing"),
                            public_jwk=_jwk(kid, key.public_key())))
    with _LOCK:
        _CACHE.clear()
    audit.record(actor, "signing_key.rotate", kid)
    return kid


def _active() -> tuple[str, Ed25519PrivateKey]:
    with _LOCK:
        if "active" in _CACHE:
            return _CACHE["active"]
    with db.session() as s:
        row = s.execute(select(db.SigningKey).where(db.SigningKey.status == "ACTIVE")).scalars().first()
        if row is None:
            kid = None
        else:
            kid, sealed = row.kid, row.private_sealed
    if kid is None:
        rotate_key()
        return _active()
    key = serialization.load_pem_private_key(crypto.open_(sealed, f"signing|{kid}", "signing"), None)
    with _LOCK:
        _CACHE["active"] = (kid, key)
    return kid, key


def jwks() -> dict:
    retain = time.time() - 30 * 86_400
    with db.session() as s:
        rows = s.execute(select(db.SigningKey)).scalars().all()
        keys = [r.public_jwk for r in rows if r.status == "ACTIVE" or (r.retired_at or 0) > retain]
    return {"keys": keys}


def _public(kid: str) -> Optional[Ed25519PublicKey]:
    with db.session() as s:
        row = s.get(db.SigningKey, kid)
        if row is None:
            return None
        x = crypto.b64d(row.public_jwk["x"])
    return Ed25519PublicKey.from_public_bytes(x)


def issue(*, tenant_id: str, customer_ref: str, txn: "db.Transaction", session_id: str,
          plan_id: str, factors: list[str], risk_band: str, floor: str, ttl_s: int,
          review_by: Optional[str] = None, s=None) -> dict:
    ttl = min(ttl_s, config.get().proof_ttl_max_s)
    iat = int(time.time())
    jti = f"pf_{uuid.uuid4().hex}"
    claims = {
        "iss": config.get().public_url, "aud": tenant_id, "sub": customer_ref,
        "jti": jti, "iat": iat, "nbf": iat - 5, "exp": iat + ttl,
        "txn": txn.id, "ext_txn": txn.external_id,
        "bnd": binding(tenant_id, customer_ref, txn.id, txn.external_id, txn.amount_minor,
                       txn.currency, txn.payee_ref, txn.type, iat),
        "amr": [f.lower() for f in factors],
        "acr": f"amfa:{floor}:{risk_band}",
        "sid": session_id, "plan": plan_id,
    }
    if review_by:
        claims["rvw"] = review_by
    kid, key = _active()
    token = jwt.encode(claims, key, algorithm=ALG, headers={"kid": kid, "typ": "amfa-proof+jwt"})
    row = db.Proof(jti=jti, tenant_id=tenant_id, session_id=session_id, transaction_id=txn.id,
                   issued_at=iat, expires_at=iat + ttl)
    detail = {"jti": jti, "amr": claims["amr"], "review": bool(review_by)}
    if s is not None:
        s.add(row)
        audit.record("system", "proof.issue", txn.id, detail, tenant_id, s=s)
    else:
        with db.session() as own:
            own.add(row)
            audit.record("system", "proof.issue", txn.id, detail, tenant_id, s=own)
    return {"token": token, "jti": jti, "expiresAt": iat + ttl}


def verify(token: str, *, tenant_id: str, customer_ref: str, external_txn: str,
           amount_minor: int, currency: str, payee_ref: str, txn_type: str,
           consume: bool, consumer: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return {"valid": False, "reason": "MALFORMED"}
    if header.get("alg") != ALG or not isinstance(header.get("kid"), str):
        return {"valid": False, "reason": "ALGORITHM_REJECTED"}
    pub = _public(header["kid"])
    if pub is None:
        return {"valid": False, "reason": "UNKNOWN_KEY"}
    try:
        claims = jwt.decode(token, pub, algorithms=[ALG], audience=tenant_id,
                            issuer=config.get().public_url,
                            options={"require": ["exp", "iat", "jti", "bnd", "sub", "aud"]})
    except jwt.ExpiredSignatureError:
        return {"valid": False, "reason": "EXPIRED"}
    except jwt.InvalidAudienceError:
        return {"valid": False, "reason": "WRONG_TENANT"}
    except jwt.PyJWTError:
        return {"valid": False, "reason": "BAD_SIGNATURE"}

    if claims["sub"] != customer_ref:
        return {"valid": False, "reason": "SUBJECT_MISMATCH"}
    if claims.get("ext_txn") != external_txn:
        return {"valid": False, "reason": "TRANSACTION_MISMATCH"}
    expected = binding(tenant_id, customer_ref, claims["txn"], external_txn, amount_minor,
                       currency, payee_ref, txn_type, claims["iat"])
    if not crypto.consteq(expected, claims["bnd"]):
        return {"valid": False, "reason": "BINDING_MISMATCH"}

    with db.session() as s:
        row = s.get(db.Proof, claims["jti"])
        if row is None or row.tenant_id != tenant_id:
            return {"valid": False, "reason": "UNKNOWN_PROOF"}
        if row.consumed_at is not None:
            return {"valid": False, "reason": "ALREADY_CONSUMED"}
        if consume:
            n = s.execute(update(db.Proof).where(db.Proof.jti == claims["jti"],
                                                 db.Proof.consumed_at.is_(None))
                          .values(consumed_at=time.time(), consumed_by=consumer[:48])).rowcount
            if n != 1:
                return {"valid": False, "reason": "ALREADY_CONSUMED"}
            txn = s.get(db.Transaction, claims["txn"])
            if txn is not None:
                txn.status, txn.updated_at = "CONSUMED", time.time()
    if consume:
        audit.record(f"apikey:{consumer}", "proof.consume", claims["txn"], {"jti": claims["jti"]}, tenant_id)
    return {"valid": True, "reason": None, "consumed": consume,
            "claims": {k: claims[k] for k in ("jti", "txn", "ext_txn", "amr", "acr", "iat", "exp")}}
