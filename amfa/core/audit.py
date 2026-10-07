from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Optional

from sqlalchemy import select, text

from amfa.core import db

GENESIS = "0" * 64
_LOCK = threading.Lock()


def _digest(prev: str, actor: str, action: str, target: str, detail: dict, at: float) -> str:
    body = db.dumps({"actor": actor, "action": action, "target": target,
                     "detail": detail, "at": round(at, 6)})
    return hashlib.sha256((prev + body).encode()).hexdigest()


def record(actor: str, action: str, target: str, detail: Optional[dict[str, Any]] = None,
           tenant_id: Optional[str] = None, s=None) -> None:
    detail = detail or {}
    if s is not None:
        with _LOCK:
            _append(s, actor, action, target, detail, tenant_id)
        return
    with _LOCK, db.session() as own:
        _append(own, actor, action, target, detail, tenant_id)


def _append(s, actor, action, target, detail, tenant_id) -> None:
    if db.is_postgres():
        s.execute(text("SELECT pg_advisory_xact_lock(727274)"))
    last = s.execute(select(db.AuditLog.hash).order_by(db.AuditLog.id.desc()).limit(1)).scalar()
    prev = last or GENESIS
    at = time.time()
    actor, action, target = actor[:80], action[:64], target[:80]
    s.add(db.AuditLog(tenant_id=tenant_id, actor=actor, action=action, target=target, detail=detail,
                      at=at, prev_hash=prev, hash=_digest(prev, actor, action, target, detail, at)))
    s.flush()


def verify(limit: int | None = None) -> dict:
    prev = GENESIS
    n = 0
    with db.session() as s:
        q = select(db.AuditLog).order_by(db.AuditLog.id)
        for row in s.execute(q).scalars():
            if row.prev_hash != prev or row.hash != _digest(prev, row.actor, row.action,
                                                            row.target, row.detail, row.at):
                return {"intact": False, "brokenAt": row.id, "checked": n}
            prev = row.hash
            n += 1
            if limit and n >= limit:
                break
    return {"intact": True, "checked": n, "head": prev}
