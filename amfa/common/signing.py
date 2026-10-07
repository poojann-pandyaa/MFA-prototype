from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode

from amfa.common import kv
from amfa.common.crypto import b64e, consteq

ALGORITHM = "AMFA-HMAC-SHA256"
MAX_SKEW_S = 300
NONCE_MIN_LEN = 16
NONCE_MAX_LEN = 128

H_KEY = "x-amfa-key-id"
H_TS = "x-amfa-timestamp"
H_NONCE = "x-amfa-nonce"
H_SIG = "x-amfa-signature"


class SignatureError(Exception):

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical(method: str, path: str, query: str, ts: str, nonce: str, body: bytes) -> bytes:
    q = urlencode(sorted(parse_qsl(query, keep_blank_values=True)))
    return "\n".join([ALGORITHM, method.upper(), path, q, ts, nonce,
                      hashlib.sha256(body).hexdigest()]).encode()


def sign(secret: str, method: str, path: str, query: str, body: bytes,
         key_id: str, ts: int | None = None, nonce: str | None = None) -> dict[str, str]:
    ts_s = str(int(ts if ts is not None else time.time()))
    nonce = nonce or secrets.token_urlsafe(18)
    mac = hmac.new(secret.encode(), canonical(method, path, query, ts_s, nonce, body),
                   hashlib.sha256).digest()
    return {"X-AMFA-Key-Id": key_id, "X-AMFA-Timestamp": ts_s,
            "X-AMFA-Nonce": nonce, "X-AMFA-Signature": b64e(mac)}


@dataclass(frozen=True)
class SignedRequest:
    key_id: str
    timestamp: int
    nonce: str
    signature: str


def parse_headers(headers) -> SignedRequest:
    key_id = headers.get(H_KEY)
    ts = headers.get(H_TS)
    nonce = headers.get(H_NONCE)
    sig = headers.get(H_SIG)
    if not (key_id and ts and nonce and sig):
        raise SignatureError("SIGNATURE_MISSING")
    if not ts.isdigit():
        raise SignatureError("TIMESTAMP_INVALID")
    if not (NONCE_MIN_LEN <= len(nonce) <= NONCE_MAX_LEN):
        raise SignatureError("NONCE_INVALID")
    if len(key_id) > 64 or len(sig) > 128:
        raise SignatureError("SIGNATURE_INVALID")
    return SignedRequest(key_id, int(ts), nonce, sig)


def verify(req: SignedRequest, secret: str, method: str, path: str, query: str,
           body: bytes, replay_scope: str) -> None:
    now = int(time.time())
    if abs(now - req.timestamp) > MAX_SKEW_S:
        raise SignatureError("TIMESTAMP_OUT_OF_WINDOW")
    expected = b64e(hmac.new(secret.encode(),
                             canonical(method, path, query, str(req.timestamp),
                                       req.nonce, body),
                             hashlib.sha256).digest())
    if not consteq(expected, req.signature):
        raise SignatureError("SIGNATURE_MISMATCH")
    if not kv.get().add_if_absent(f"nonce:{replay_scope}:{req.key_id}:{req.nonce}",
                                  2 * MAX_SKEW_S):
        raise SignatureError("NONCE_REPLAYED")
