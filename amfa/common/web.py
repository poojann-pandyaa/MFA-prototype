from __future__ import annotations

import ipaddress
import json
import logging
import time
import uuid
from typing import Callable, Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from amfa import config
from amfa.common import kv

log = logging.getLogger("amfa.http")


class ApiError(HTTPException):

    def __init__(self, status: int, code: str, message: str | None = None,
                 headers: dict | None = None):
        super().__init__(status_code=status, detail={"code": code, "message": message or code},
                         headers=headers)
        self.code = code


class BodyLimit:
    def __init__(self, app: ASGIApp, limits: Iterable[tuple[str, int]], default: int = 64 * 1024):
        self.app = app
        self.limits = sorted(limits, key=lambda x: -len(x[0]))
        self.default = default

    def _limit(self, path: str) -> int:
        for prefix, n in self.limits:
            if path.startswith(prefix):
                return n
        return self.default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope["path"])
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > limit:
                        await _reject(send, 413, "BODY_TOO_LARGE")
                        return
                except ValueError:
                    await _reject(send, 400, "BAD_CONTENT_LENGTH")
                    return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _TooLarge()
            return msg

        try:
            await self.app(scope, limited, send)
        except _TooLarge:
            await _reject(send, 413, "BODY_TOO_LARGE")


class _TooLarge(Exception):
    pass


async def _reject(send: Send, status: int, code: str) -> None:
    body = json.dumps({"error": {"code": code, "message": code}}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


CSP_DEFAULT = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
               "media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class SecurityHeaders:
    def __init__(self, app: ASGIApp, frame_ancestors: Callable[[Scope], str | None] | None = None):
        self.app = app
        self.frame_ancestors = frame_ancestors
        self.production = config.get().production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        ancestors = self.frame_ancestors(scope) if self.frame_ancestors else None

        async def send_with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                h = [(k, v) for k, v in msg.get("headers", [])
                     if k.lower() not in (b"server", b"x-powered-by")]
                csp = CSP_DEFAULT
                xfo = b"DENY"
                if ancestors:
                    csp = csp.replace("frame-ancestors 'none'", f"frame-ancestors {ancestors}")
                    xfo = None
                h += [
                    (b"content-security-policy", csp.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"permissions-policy",
                     b"camera=(self), microphone=(self), geolocation=(), payment=(), usb=()"),
                ]
                if xfo:
                    h.append((b"x-frame-options", xfo))
                if self.production:
                    h.append((b"strict-transport-security",
                              b"max-age=63072000; includeSubDomains; preload"))
                if not path.startswith(("/static/", "/sdk/")):
                    h.append((b"cache-control", b"no-store"))
                    h.append((b"pragma", b"no-cache"))
                msg["headers"] = h
            await send(msg)

        await self.app(scope, receive, send_with_headers)


def _trusted(peer: str, proxies: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for p in proxies:
        try:
            if ip in ipaddress.ip_network(p, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "0.0.0.0"
    proxies = config.get().trusted_proxies
    if not proxies or not _trusted(peer, proxies):
        return peer
    xff = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _trusted(hop, proxies):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer
    return peer


def rate_limit(key: str, limit: int, window_s: int) -> None:
    n = kv.get().incr(f"rl:{key}:{int(time.time() // window_s)}", window_s + 1)
    if n > limit:
        raise ApiError(429, "RATE_LIMITED", "too many requests",
                       headers={"Retry-After": str(window_s)})


def install(app: FastAPI, body_limits: Iterable[tuple[str, int]] = (),
            frame_ancestors: Callable[[Scope], str | None] | None = None) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        if not (8 <= len(rid) <= 64 and rid.replace("-", "").isalnum()):
            rid = uuid.uuid4().hex
        request.state.request_id = rid
        request.state.client_ip = client_ip(request)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info("request", extra={"rid": rid, "method": request.method,
                                   "path": request.url.path, "status": response.status_code,
                                   "ms": int((time.perf_counter() - started) * 1000)})
        return response

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code,
                            headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {
            "code": _code_for(exc.status_code), "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        fields = [{"loc": [str(x) for x in e.get("loc", [])][:6], "msg": e.get("msg", "")[:120]}
                  for e in exc.errors()[:20]]
        return JSONResponse({"error": {"code": "VALIDATION_FAILED", "message": "invalid request",
                                       "fields": fields}}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "-")
        log.exception("unhandled error rid=%s", rid)
        return JSONResponse({"error": {"code": "INTERNAL_ERROR",
                                       "message": f"internal error (request {rid})"}},
                            status_code=500)

    app.add_middleware(BodyLimit, limits=list(body_limits))
    app.add_middleware(SecurityHeaders, frame_ancestors=frame_ancestors)


def _code_for(status: int) -> str:
    return {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 413: "BODY_TOO_LARGE",
            422: "VALIDATION_FAILED", 429: "RATE_LIMITED"}.get(status, "ERROR")
