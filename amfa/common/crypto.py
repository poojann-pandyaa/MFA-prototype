from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from amfa import config

_PH = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
_SEAL_PREFIX = "amfa1"


class DecryptionError(ValueError):
    pass


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def derive_key(purpose: str, version: str | None = None, length: int = 32) -> bytes:
    s = config.get()
    master = s.master_keys[version or s.active_key_version]
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None,
                info=f"amfa:{purpose}".encode()).derive(master)


def seal(plaintext: bytes, aad: str, purpose: str = "data") -> str:
    s = config.get()
    ver = s.active_key_version
    key = derive_key(f"seal:{purpose}", ver)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, f"{purpose}|{aad}".encode())
    return f"{_SEAL_PREFIX}.{ver}.{b64e(nonce)}.{b64e(ct)}"


def open_(sealed: str, aad: str, purpose: str = "data") -> bytes:
    try:
        prefix, ver, nonce_b64, ct_b64 = sealed.split(".")
    except ValueError as exc:
        raise DecryptionError("malformed ciphertext") from exc
    if prefix != _SEAL_PREFIX:
        raise DecryptionError("unknown ciphertext format")
    if ver not in config.get().master_keys:
        raise DecryptionError(f"ciphertext uses retired key version {ver}")
    key = derive_key(f"seal:{purpose}", ver)
    try:
        return AESGCM(key).decrypt(b64d(nonce_b64), b64d(ct_b64), f"{purpose}|{aad}".encode())
    except Exception as exc:
        raise DecryptionError("ciphertext failed authentication") from exc


def needs_rotation(sealed: str) -> bool:
    try:
        return sealed.split(".")[1] != config.get().active_key_version
    except IndexError:
        return True


def token(prefix: str, nbytes: int = 32) -> str:
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def _index_key(purpose: str) -> bytes:
    return derive_key(purpose, config.get().index_key_version)


def hash_token(value: str) -> str:
    return hmac.new(_index_key("token-index"), value.encode(), hashlib.sha256).hexdigest()


def keyed_hash(value: str, purpose: str, length: int = 32) -> str:
    return hmac.new(_index_key(f"pseudonym:{purpose}"), value.encode(),
                    hashlib.sha256).hexdigest()[:length]


def consteq(a: str | bytes, b: str | bytes) -> bool:
    if isinstance(a, str):
        a = a.encode()
    if isinstance(b, str):
        b = b.encode()
    return hmac.compare_digest(a, b)


def hash_password(password: str) -> str:
    return _PH.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _PH.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(stored: str) -> bool:
    try:
        return _PH.check_needs_rehash(stored)
    except InvalidHashError:
        return True


_DUMMY_HASH = _PH.hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
