from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx

from amfa import config
from amfa.common import signing
from amfa.common.contract import EnrollResult, FactorResult, PadMode, unavailable

log = logging.getLogger("amfa.factors")

_clients: dict[str, httpx.Client] = {}
_mounted: dict[str, Any] = {}


def base(factor: str) -> str:
    s = config.get()
    return {"FACE": s.face_url, "VOICE": s.voice_url, "UEBA": s.ueba_url}[factor].rstrip("/")


def mount(factor: str, client: httpx.Client) -> None:
    _mounted[factor] = client


def _client(factor: str) -> httpx.Client:
    if factor in _mounted:
        return _mounted[factor]
    c = _clients.get(factor)
    if c is None:
        c = httpx.Client(base_url=base(factor), timeout=config.get().factor_timeout_s,
                         headers={"user-agent": "amfa-core/1.0"})
        _clients[factor] = c
    return c


MAX_REQUEST_BYTES = {"FACE": 16 * 1024 * 1024, "VOICE": 24 * 1024 * 1024, "UEBA": 3 * 1024 * 1024}


class CaptureRejected(Exception):
    def __init__(self, code: str):
        self.code = code


def _encode(body: dict) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _call(factor: str, method: str, path: str, body: Optional[dict] = None) -> tuple[int, Any]:
    try:
        raw = _encode(body) if body is not None else b""
    except ValueError:
        raise CaptureRejected("CAPTURE_MALFORMED") from None
    if len(raw) > MAX_REQUEST_BYTES.get(factor, 4 * 1024 * 1024):
        raise CaptureRejected("CAPTURE_TOO_LARGE")
    headers = signing.sign(config.get().internal_token, method, path, "", raw, "core")
    headers["content-type"] = "application/json"
    r = _client(factor).request(method, path, content=raw, headers=headers)
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, data


def verify(factor: str, *, tenant: str, user: str, session: str, txn: str,
           challenge: dict, payload: dict, pad_mode: str) -> FactorResult:
    t0 = time.perf_counter()
    try:
        code, data = _call(factor, "POST", "/factor/verify", {
            "tenantId": tenant, "userId": user, "sessionId": session, "transactionId": txn,
            "challenge": challenge, "payload": payload, "padMode": PadMode(pad_mode).value})
        ms = int((time.perf_counter() - t0) * 1000)
        if code in (400, 413, 415, 422):
            return FactorResult(factor=factor, status="FAILED", failureReason=f"CAPTURE_REJECTED_{code}",
                                latencyMs=ms)
        if code != 200 or not isinstance(data, dict):
            return unavailable(factor, f"HTTP_{code}", ms)
        res = FactorResult.model_validate(data)
        if res.factor != factor:
            return unavailable(factor, "CONTRACT_VIOLATION")
        return res
    except CaptureRejected as exc:
        return FactorResult(factor=factor, status="FAILED", failureReason=exc.code,
                            latencyMs=int((time.perf_counter() - t0) * 1000))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("factor %s unavailable: %s", factor, type(exc).__name__)
        return unavailable(factor, type(exc).__name__, int((time.perf_counter() - t0) * 1000))


def enroll(factor: str, *, tenant: str, user: str, payload: dict) -> EnrollResult:
    try:
        try:
            code, data = _call(factor, "POST", "/factor/enroll",
                               {"tenantId": tenant, "userId": user, "payload": payload})
        except CaptureRejected as exc:
            return EnrollResult(factor=factor, enrolled=False, reason=exc.code)
        if code != 200 or not isinstance(data, dict):
            return EnrollResult(factor=factor, enrolled=False, reason=f"SERVICE_HTTP_{code}")
        return EnrollResult.model_validate(data)
    except (httpx.HTTPError, ValueError) as exc:
        return EnrollResult(factor=factor, enrolled=False, reason=f"SERVICE_{type(exc).__name__}")


def erase(factor: str, tenant: str, user: str) -> bool:
    try:
        code, data = _call(factor, "DELETE", f"/factor/templates/{tenant}/{user}")
        return code == 200
    except httpx.HTTPError:
        return False


def ueba(path: str, body: dict) -> Optional[dict]:
    try:
        code, data = _call("UEBA", "POST", path, body)
        return data if code == 200 and isinstance(data, dict) else None
    except httpx.HTTPError:
        return None


def ueba_phrase() -> Optional[dict]:
    try:
        code, data = _call("UEBA", "GET", "/enroll/phrase")
        return data if code == 200 else None
    except httpx.HTTPError:
        return None


def health() -> dict:
    out = {}
    for f in ("FACE", "VOICE", "UEBA"):
        try:
            r = _client(f).get("/health", timeout=3.0)
            out[f] = r.json().get("status", "unknown") if r.status_code == 200 else f"HTTP_{r.status_code}"
        except Exception as exc:
            out[f] = f"down ({type(exc).__name__})"
    return out
