"""The band store when the band switch runs as its own service (AMFA_BANDS_URL set): the same calls as the local store, made over
signed internal requests to amfa/bands/service.py.

Failing safe, call by call:
  decision_state   one call per payment, waiting at most AMFA_BANDS_TIMEOUT_S (0.2 s). Slow, down or odd: COLLECTING, the rules
                   set the minimum. An outage can only add checks.
  due              unreachable: not due. Nothing is graded; a band on MODEL is then decided by the rules once its last grading is
                   two days old.
  add_row, label   unreachable: kept in the core's outbox (band_outbox) and sent again by the sweeper; dropped once the link they
                   carry would have expired, so the outbox never keeps anything longer than the row itself would.
  everything else  (reading a band for an approval, moving, falling back, promoting, purging) raises BandsUnavailable: the
                   operator's action fails and nothing is half-applied; promotion never reaches the pointer switch.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from types import SimpleNamespace
from typing import Any, Optional

import httpx
from sqlalchemy import select

from amfa import config
from amfa.bands.engine import COLLECTING, ELIGIBLE, MODEL
from amfa.common import signing

log = logging.getLogger("amfa.bands")
_client: Optional[httpx.Client] = None
_mounted: Optional[Any] = None
OUTBOX_BATCH = 200
OUTBOX_BACKOFF_S = (30, 120, 600, 1800, 3600)


class BandsUnavailable(Exception):
    """The band service could not be reached or refused the call. Carries the same fields as a GateError, for the admin API."""
    code, status = "BANDS_UNAVAILABLE", 503

    def __init__(self, message: str = "the band service is unavailable"):
        super().__init__(message)
        self.message = message


def mount(client) -> None:
    """Tests: send every call to [client] (for instance the service's TestClient) instead of the network."""
    global _mounted
    _mounted = client


def _http():
    global _client
    if _mounted is not None:
        return _mounted
    if _client is None:
        _client = httpx.Client(base_url=config.get().bands_url, headers={"user-agent": "amfa-core/1.0"})
    return _client


def call(path: str, body: dict, timeout: float = 10.0) -> dict:
    """POST one signed request to the band service. Raises BandsUnavailable on anything but a 200 with a JSON object."""
    raw = json.dumps(body, separators=(",", ":"), default=str).encode()
    headers = signing.sign(config.get().internal_token, "POST", path, "", raw, "core") | {"content-type": "application/json"}
    try:
        r = _http().post(path, content=raw, headers=headers, timeout=timeout)
    except Exception as e:
        raise BandsUnavailable(f"band service unreachable ({type(e).__name__})") from None
    if r.status_code != 200:
        raise BandsUnavailable(f"band service answered {r.status_code}")
    try:
        out = r.json()
    except ValueError:
        raise BandsUnavailable("band service answered with something that is not JSON") from None
    if not isinstance(out, dict):
        raise BandsUnavailable("band service answered with something unexpected")
    return out


def _ns(d: Optional[dict]):
    return None if d is None else SimpleNamespace(**d)


class RemoteStore:
    remote = True

    def __init__(self, s=None):
        self.s = s                  # the core session, for the outbox

    # grading and the decision
    def evaluate(self, tenant_id, collection, timezone, version, candidate, now):
        out = call("/v1/evaluate", {"tenantId": tenant_id, "collection": collection.model_dump(mode="json"), "timezone": timezone,
                                    "version": version, "candidate": candidate, "now": now}, timeout=120)
        return {int(k): _ns(v) for k, v in (out.get("states") or {}).items()}, out.get("events") or []

    def due(self, tenant_id: str, now: float) -> bool:
        try:
            return bool(call("/v1/due", {"tenantId": tenant_id, "now": now}).get("due"))
        except BandsUnavailable:
            return False

    def decision_state(self, tenant_id: str, band: int, now: float) -> tuple[str, Optional[str]]:
        try:
            out = call("/v1/decision-state", {"tenantId": tenant_id, "band": band, "now": now}, timeout=config.get().bands_timeout_s)
        except BandsUnavailable:
            return COLLECTING, None
        state, version = out.get("state"), out.get("version")
        if state == MODEL and isinstance(version, str) and version:
            return MODEL, version
        return (state, None) if state in (COLLECTING, ELIGIBLE) else (COLLECTING, None)

    # reading a band
    def band(self, tenant_id: str, band: int) -> Optional[dict]:
        return call("/v1/band", {"tenantId": tenant_id, "band": band}).get("band")

    def candidate(self, tenant_id: str, band: int) -> Optional[dict]:
        return call("/v1/candidate", {"tenantId": tenant_id, "band": band}).get("candidate")

    # moving a band
    def move_to_model(self, tenant_id, band, version, switched_week, now) -> Optional[str]:
        return call("/v1/move-to-model", {"tenantId": tenant_id, "band": band, "version": version, "switchedWeek": switched_week,
                                          "now": now}).get("error")

    def approve_candidate(self, tenant_id, band, version, approved_by, now) -> Optional[str]:
        return call("/v1/approve-candidate", {"tenantId": tenant_id, "band": band, "version": version, "approvedBy": approved_by,
                                              "now": now}).get("error")

    def fallback(self, tenant_id, band, reason, now, actor="system:band-gate"):
        out = call("/v1/fallback", {"tenantId": tenant_id, "band": band, "reason": reason, "now": now, "actor": actor})
        return bool(out.get("changed")), out.get("events") or []

    def reset_all(self, tenant_id: str) -> None:
        call("/v1/reset", {"tenantId": tenant_id})

    # promotion
    def promotion_plan(self, tenant_ids: list[str], candidate: str) -> dict:
        return call("/v1/promotion-plan", {"tenantIds": tenant_ids, "candidate": candidate}).get("plan") or {}

    def promote(self, plan, candidate, timezones, operator, now) -> list:
        return call("/v1/promote", {"plan": plan, "candidate": candidate, "timezones": timezones, "operator": operator,
                                    "now": now}, timeout=120).get("events") or []

    # rows and labels: never lost to an outage, held in the outbox until the service takes them
    def add_row(self, fields: dict, now: float):
        try:
            out = call("/v1/rows", {"fields": fields, "now": now})
        except BandsUnavailable:
            self._hold("ROW", {"fields": fields, "now": now}, fields.get("link_expires_at") or now)
            return None, []
        return (SimpleNamespace(**fields, id=out.get("id")) if out.get("stored") else None), out.get("events") or []

    def apply_label(self, link: str, label: str, source: str, now: float):
        try:
            out = call("/v1/labels", {"link": link, "label": label, "source": source, "now": now})
        except BandsUnavailable:
            self._hold("LABEL", {"link": link, "label": label, "source": source, "now": now},
                       now + config.get().retention_days * 86_400.0)
            return False, []
        return bool(out.get("applied")), out.get("events") or []

    def after_fraud_label(self, row, now: float) -> list:
        return []                   # the service checks a fresh fraud label itself, when the row or label arrives

    def purge(self, now: float, retention: dict) -> dict:
        return call("/v1/purge", {"now": now, "retention": {k: list(v) for k, v in retention.items()}}, timeout=120)

    def _hold(self, kind: str, payload: dict, expires_at: float) -> None:
        from amfa.core import db
        if self.s is None:
            return
        self.s.add(db.BandOutbox(id=uuid.uuid4().hex, kind=kind, payload=payload, attempts=0, next_at=time.time(),
                                 expires_at=expires_at, created_at=time.time()))
        self.s.flush()
        log.warning("band service unavailable; held in the outbox", extra={"kind": kind})


def flush_outbox(s, now: Optional[float] = None) -> dict[str, int]:
    """The sweeper's call: send what the outbox holds, oldest first. Delivered: gone, and its events written into the core.
    Still unreachable: tried again later, less often. Its link would have expired by now: dropped, never sent."""
    from amfa.core import band_gate, db
    now = now or time.time()
    out = {"sent": 0, "waiting": 0, "dropped": 0}
    items = s.execute(select(db.BandOutbox).where(db.BandOutbox.next_at <= now)
                      .order_by(db.BandOutbox.created_at).limit(OUTBOX_BATCH)).scalars().all()
    for it in items:
        if it.expires_at <= now:
            s.delete(it)
            out["dropped"] += 1
            continue
        try:
            path = "/v1/rows" if it.kind == "ROW" else "/v1/labels"
            reply = call(path, it.payload)
        except BandsUnavailable:
            it.attempts = (it.attempts or 0) + 1
            it.next_at = now + OUTBOX_BACKOFF_S[min(it.attempts, len(OUTBOX_BACKOFF_S)) - 1]
            out["waiting"] += 1
            continue
        band_gate.apply_events(s, reply.get("events") or [])
        s.delete(it)
        out["sent"] += 1
    s.flush()
    return out
