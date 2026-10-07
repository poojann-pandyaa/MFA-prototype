from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from starlette.types import ASGIApp, Receive, Scope, Send

from amfa import config
from amfa.common import logs, web
from amfa.core import api_admin, api_client, api_tenant, db, factors, orchestrator, proofs, webhooks
from amfa.engine.agent import backend_status, build_agent

log = logging.getLogger("amfa.core")
STATIC = Path(__file__).resolve().parent / "static"


_origins_cache: dict[str, tuple[float, list[str]]] = {}


def tenant_origins(slug: str | None) -> list[str]:
    if not slug:
        return []
    hit = _origins_cache.get(slug)
    if hit and hit[0] > time.time():
        return hit[1]
    with db.session() as s:
        t = s.execute(select(db.Tenant).where(db.Tenant.slug == slug)).scalars().first()
        origins = list((t.policy or {}).get("allowedEmbedOrigins", [])) if t else []
    _origins_cache[slug] = (time.time() + 60, origins)
    return origins


def all_origins() -> set[str]:
    out = set(config.get().cors_origins)
    with db.session() as s:
        for t in s.execute(select(db.Tenant)).scalars():
            out.update((t.policy or {}).get("allowedEmbedOrigins", []))
    return out


def frame_ancestors(scope: Scope) -> str | None:
    if not scope["path"].startswith("/hosted/"):
        return None
    from urllib.parse import parse_qs
    slug = (parse_qs(scope.get("query_string", b"").decode()).get("t") or [None])[0]
    origins = tenant_origins(slug) if slug and slug.replace("-", "").isalnum() else []
    return " ".join(origins) if origins else None


class ClientCors:

    def __init__(self, app: ASGIApp):
        self.app = app
        self._cache: tuple[float, set[str]] = (0.0, set())

    def allowed(self, origin: str) -> bool:
        if self._cache[0] < time.time():
            self._cache = (time.time() + 60, all_origins())
        return origin in self._cache[1]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/c/v1/"):
            await self.app(scope, receive, send)
            return
        origin = dict(scope.get("headers", [])).get(b"origin", b"").decode()
        ok = bool(origin) and self.allowed(origin)
        if scope["method"] == "OPTIONS" and ok:
            await send({"type": "http.response.start", "status": 204, "headers": [
                (b"access-control-allow-origin", origin.encode()),
                (b"access-control-allow-methods", b"GET, POST"),
                (b"access-control-allow-headers", b"authorization, content-type"),
                (b"access-control-max-age", b"600"), (b"vary", b"Origin")]})
            await send({"type": "http.response.body", "body": b""})
            return

        async def with_cors(msg):
            if msg["type"] == "http.response.start" and ok:
                msg["headers"] = list(msg.get("headers", [])) + [
                    (b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]
            await send(msg)
        await self.app(scope, receive, with_cors)


class Sweeper(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="amfa-sweeper")
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(15):
            try:
                with db.session() as s:
                    orchestrator.expire_stale(s)
            except Exception:
                log.exception("session sweep failed")

    def stop(self) -> None:
        self._stop.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup("core")
    db.migrate()
    if not proofs.jwks()["keys"]:
        proofs.rotate_key("bootstrap")
    agent = build_agent()
    if agent is not None and hasattr(agent, "warm"):
        try:
            log.info("agent warm in %d ms (%s)", agent.warm(), agent.name)
        except Exception as exc:
            log.warning("agent warm-up failed; rule table will answer: %s", exc)
    workers = []
    if config.get().env != "test":
        workers = [webhooks.Dispatcher(), Sweeper()]
        for w in workers:
            w.start()
    yield
    for w in workers:
        w.stop()


def create_app() -> FastAPI:
    s = config.get()
    app = FastAPI(title="Adaptive MFA", version="1.0.0", lifespan=lifespan,
                  description="Adaptive, risk-based multi-factor authentication for transaction "
                              "authorisation. See docs/INTEGRATION.md.",
                  docs_url=None if s.production else "/docs", redoc_url=None,
                  openapi_url=None if s.production else "/openapi.json")
    web.install(app, body_limits=[("/c/v1/enrollment/face", 20 * 1024 * 1024),
                                  ("/c/v1/enrollment/voice", 32 * 1024 * 1024),
                                  ("/c/v1/session/factors/", 20 * 1024 * 1024),
                                  ("/c/v1/enrollment/behaviour", 2 * 1024 * 1024),
                                  ("/v1/", 256 * 1024)],
                frame_ancestors=frame_ancestors)
    app.add_middleware(ClientCors)
    app.include_router(api_tenant.router)
    app.include_router(api_client.router)
    app.include_router(api_admin.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/c/v1/embed-origins", include_in_schema=False)
    def embed_origins(t: str = "") -> dict:
        return {"origins": tenant_origins(t) if t.replace("-", "").isalnum() else []}

    @app.get("/.well-known/jwks.json", include_in_schema=False)
    def jwks() -> Response:
        return JSONResponse(proofs.jwks(), headers={"cache-control": "public, max-age=300"})

    @app.get("/sdk/amfa.js", include_in_schema=False)
    def sdk() -> FileResponse:
        return FileResponse(STATIC / "sdk" / "amfa.js", media_type="text/javascript",
                            headers={"cache-control": "public, max-age=300"})

    for page, file in (("/hosted/enroll", "enroll.html"), ("/hosted/challenge", "challenge.html"),
                       ("/hosted/device", "device.html"), ("/admin", "admin.html")):
        def _serve(file=file) -> FileResponse:
            return FileResponse(STATIC / "pages" / file, media_type="text/html")
        app.add_api_route(page, _serve, methods=["GET"], include_in_schema=False)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"service": "adaptive-mfa", "docs": "/docs" if not s.production else None,
                "jwks": "/.well-known/jwks.json"}

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "up"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> Response:
        checks = {}
        try:
            with db.session() as sess:
                sess.execute(text("SELECT 1"))
            checks["database"] = "up"
        except Exception as exc:
            checks["database"] = f"down ({type(exc).__name__})"
        checks["factors"] = factors.health()
        checks["agent"] = backend_status()
        ok = checks["database"] == "up" and all(v == "up" for v in checks["factors"].values())
        return JSONResponse({"ready": ok, **checks}, status_code=200 if ok else 503)

    return app


app = create_app()
