from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any, Callable, Iterator, Optional

import numpy as np
from fastapi import Request
from sqlalchemy import (Column, Float, Integer, LargeBinary, MetaData, String, Table, Text,
                        create_engine, delete, insert, select, update)

from amfa import config
from amfa.common import crypto, signing
from amfa.common.web import ApiError

PROBE_HISTORY = 25
PROBE_RETENTION_S = 30 * 86_400


class TemplateStore:
    def __init__(self, factor: str, url: Optional[str] = None):
        s = config.get()
        self.factor = factor
        url = url or _factor_db_url(factor)
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            s.data_dir.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine = create_engine(url, **kwargs)
        md = MetaData()
        self.templates = Table(
            f"{factor}_template", md,
            Column("tenant_id", String(64), primary_key=True),
            Column("user_id", String(64), primary_key=True),
            Column("sealed", Text, nullable=False),
            Column("meta", Text, nullable=False, default="{}"),
            Column("created_at", Float, nullable=False),
            Column("updated_at", Float, nullable=False),
        )
        self.probes = Table(
            f"{factor}_probe", md,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("tenant_id", String(64), nullable=False, index=True),
            Column("user_id", String(64), nullable=False, index=True),
            Column("digest", String(64), nullable=False),
            Column("vector", LargeBinary, nullable=True),
            Column("at", Float, nullable=False),
        )
        md.create_all(self.engine)

    def _aad(self, tenant: str, user: str) -> str:
        return f"{self.factor}|{tenant}|{user}"

    def put(self, tenant: str, user: str, template: dict, meta: dict) -> None:
        sealed = crypto.seal(json.dumps(template).encode(), self._aad(tenant, user),
                             purpose=f"template:{self.factor}")
        now = time.time()
        with self.engine.begin() as c:
            exists = c.execute(select(self.templates.c.user_id).where(
                self.templates.c.tenant_id == tenant,
                self.templates.c.user_id == user)).first()
            if exists:
                c.execute(update(self.templates).where(
                    self.templates.c.tenant_id == tenant, self.templates.c.user_id == user
                ).values(sealed=sealed, meta=json.dumps(meta), updated_at=now))
            else:
                c.execute(insert(self.templates).values(
                    tenant_id=tenant, user_id=user, sealed=sealed, meta=json.dumps(meta),
                    created_at=now, updated_at=now))

    def get(self, tenant: str, user: str) -> Optional[dict]:
        with self.engine.connect() as c:
            row = c.execute(select(self.templates.c.sealed).where(
                self.templates.c.tenant_id == tenant,
                self.templates.c.user_id == user)).first()
        if row is None:
            return None
        return json.loads(crypto.open_(row.sealed, self._aad(tenant, user),
                                       purpose=f"template:{self.factor}"))

    def all(self, tenant: str) -> Iterator[tuple[str, dict]]:
        """Every template of a tenant, decrypted one at a time (for 1:N search)."""
        with self.engine.connect() as c:
            rows = c.execute(select(self.templates.c.user_id, self.templates.c.sealed).where(
                self.templates.c.tenant_id == tenant)).all()
        for r in rows:
            yield r.user_id, json.loads(crypto.open_(r.sealed, self._aad(tenant, r.user_id),
                                                     purpose=f"template:{self.factor}"))

    def delete(self, tenant: str, user: str) -> bool:
        with self.engine.begin() as c:
            n = c.execute(delete(self.templates).where(
                self.templates.c.tenant_id == tenant, self.templates.c.user_id == user)).rowcount
            c.execute(delete(self.probes).where(
                self.probes.c.tenant_id == tenant, self.probes.c.user_id == user))
        return bool(n)

    def ping(self) -> bool:
        with self.engine.connect() as c:
            c.execute(select(1))
        return True


class ReplayGuard:

    def __init__(self, store: TemplateStore, identical_cos: float = 0.9999,
                 same: Optional[Callable[[np.ndarray, np.ndarray], bool]] = None):
        self.store = store
        self.identical_cos = identical_cos
        self.same = same or (lambda a, b: _cos(a, b) >= self.identical_cos)

    def check(self, tenant: str, user: str, digest: str,
              vector: Optional[np.ndarray]) -> Optional[str]:
        t = self.store.probes
        cutoff = time.time() - PROBE_RETENTION_S
        with self.store.engine.connect() as c:
            rows = c.execute(select(t.c.digest, t.c.vector).where(
                t.c.tenant_id == tenant, t.c.user_id == user, t.c.at >= cutoff
            ).order_by(t.c.id.desc()).limit(PROBE_HISTORY)).all()
        for r in rows:
            if crypto.consteq(r.digest, digest):
                return "REPLAY_IDENTICAL_CAPTURE"
            if vector is not None and r.vector:
                prev = np.frombuffer(crypto.open_(r.vector.decode(), f"{tenant}|{user}",
                                                  purpose=f"probe:{self.store.factor}"),
                                     dtype=np.float32)
                if prev.shape == vector.shape and self.same(prev, vector):
                    return "REPLAY_IDENTICAL_SIGNAL"
        return None

    def remember(self, tenant: str, user: str, digest: str,
                 vector: Optional[np.ndarray]) -> None:
        t = self.store.probes
        sealed = None
        if vector is not None:
            sealed = crypto.seal(np.asarray(vector, np.float32).tobytes(), f"{tenant}|{user}",
                                 purpose=f"probe:{self.store.factor}").encode()
        with self.store.engine.begin() as c:
            c.execute(insert(t).values(tenant_id=tenant, user_id=user, digest=digest,
                                       vector=sealed, at=time.time()))
            ids = [r.id for r in c.execute(select(t.c.id).where(
                t.c.tenant_id == tenant, t.c.user_id == user
            ).order_by(t.c.id.desc()).offset(PROBE_HISTORY)).all()]
            if ids:
                c.execute(delete(t).where(t.c.id.in_(ids)))


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(a @ b / (na * nb))


def _factor_db_url(factor: str) -> str:
    import os
    return os.getenv(f"AMFA_{factor.upper()}_DB_URL") or \
        f"sqlite:///{config.get().data_dir / f'{factor}.db'}"


async def internal_auth(request: Request) -> None:
    body = await request.body()
    try:
        req = signing.parse_headers(request.headers)
        if req.key_id != "core":
            raise signing.SignatureError("UNKNOWN_CALLER")
        signing.verify(req, config.get().internal_token, request.method, request.url.path,
                       request.url.query, body, replay_scope="internal")
    except signing.SignatureError as exc:
        raise ApiError(401, exc.code, "internal authentication failed") from None


def normalise(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


@lru_cache(maxsize=None)
def store_for(factor: str) -> TemplateStore:
    return TemplateStore(factor)
