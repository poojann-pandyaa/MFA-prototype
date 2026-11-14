"""End-to-end encryption of what a phone sends AMFA (pictures, recordings, typing), on top of HTTPS.

HTTPS protects the hop to whatever terminates it: a tunnel, a load balancer, a proxy. This protects the content itself, so only
the AMFA core (which holds the private key) can read it, whoever carries it. It is ECIES with standard parts:

    client: new P-256 key e;  shared = ECDH(e, server public key)
            key = HKDF-SHA256(shared, salt = nonce, info = "amfa-e2e-v1|" + kid + "|" + aad), 32 bytes
            box = AES-256-GCM(key, nonce, plaintext, aad)

`aad` ties the box to one use ("session id|factor|challenge id", or "enroll|SHA-256 of the enrolment token|factor"), so a captured box cannot be
replayed into another session, challenge or factor. The server's key pairs come from the master keys, one per version, so
rotating a master key rotates this key and boxes made under an older version still open while that version is kept.

A box on the wire is {"kid", "epk", "nonce", "ct"}, all base64url. What the client gets from /c/v1/e2e-key is
{"kid", "alg", "publicKey", "fingerprint"}; a client that wants to resist a proxy that rewrites that answer pins the fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from amfa import config
from amfa.common.crypto import DecryptionError, b64d, b64e

ALG = "ecdh-p256+hkdf-sha256+aes256gcm"
_CURVE = ec.SECP256R1()
_N = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)


def _private(version: str) -> ec.EllipticCurvePrivateKey:
    master = config.get().master_keys[version]
    seed = HKDF(algorithm=hashes.SHA256(), length=48, salt=None, info=b"amfa:e2e-private").derive(master)
    return ec.derive_private_key(int.from_bytes(seed, "big") % (_N - 1) + 1, _CURVE)


def _public_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def kid_of(version: str) -> str:
    return f"{version}.{hashlib.sha256(_public_bytes(_private(version))).hexdigest()[:12]}"


def public_key() -> dict:
    """What a phone fetches before its first encrypted submission."""
    v = config.get().active_key_version
    raw = _public_bytes(_private(v))
    return {"kid": kid_of(v), "alg": ALG, "publicKey": b64e(raw), "fingerprint": hashlib.sha256(raw).hexdigest()}


def _derive(shared: bytes, nonce: bytes, kid: str, aad: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=nonce, info=f"amfa-e2e-v1|{kid}|{aad}".encode()).derive(shared)


def is_box(payload: Any) -> bool:
    return isinstance(payload, dict) and set(payload) == {"e2e"} and isinstance(payload["e2e"], dict)


def decrypt(box: dict, aad: str) -> dict:
    """Open a box made for [aad]; returns the JSON object inside. Raises DecryptionError for anything wrong."""
    try:
        kid = box["kid"]
        version = kid.split(".", 1)[0]
        if version not in config.get().master_keys or kid != kid_of(version):
            raise DecryptionError("unknown key")
        epk = ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, b64d(box["epk"]))
        nonce = b64d(box["nonce"])
        if len(nonce) != 12:
            raise DecryptionError("bad nonce")
        key = _derive(_private(version).exchange(ec.ECDH(), epk), nonce, kid, aad)
        out = json.loads(AESGCM(key).decrypt(nonce, b64d(box["ct"]), aad.encode()))
    except DecryptionError:
        raise
    except Exception as exc:                      # a wrong key, a changed byte or a different aad all end up here
        raise DecryptionError("cannot open the box") from exc
    if not isinstance(out, dict):
        raise DecryptionError("not an object")
    return out


def encrypt(public: dict, plaintext: dict, aad: str) -> dict:
    """What a client does (used by tests and Python clients; the Android app does the same with the platform's crypto)."""
    server = ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, b64d(public["publicKey"]))
    eph = ec.generate_private_key(_CURVE)
    nonce = os.urandom(12)
    key = _derive(eph.exchange(ec.ECDH(), server), nonce, public["kid"], aad)
    ct = AESGCM(key).encrypt(nonce, json.dumps(plaintext, separators=(",", ":")).encode(), aad.encode())
    return {"kid": public["kid"], "epk": b64e(_public_bytes(eph)), "nonce": b64e(nonce), "ct": b64e(ct)}
