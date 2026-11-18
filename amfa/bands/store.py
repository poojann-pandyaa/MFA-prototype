"""The one way the core reaches band data (amfa/bands/engine.py).

  LocalStore    the band switch runs inside the core, in the core's own database and the caller's own transaction (the default)
  RemoteStore   the band switch runs as its own service with its own database (amfa/bands/service.py), reached over signed
                internal requests; chosen when AMFA_BANDS_URL is set (amfa/bands/remote.py)

Both answer the same calls. Calls that change band data also return the events the core must record (audit entries, security
events, two-person requests to cancel).
"""
from __future__ import annotations

from typing import Optional

from amfa.bands import engine
from amfa.bands.engine import Events


class LocalStore:
    remote = False

    def __init__(self, s):
        self.bs = s

    # grading and the decision
    def evaluate(self, tenant_id, collection, timezone, version, candidate, now):
        ev = Events()
        return engine.evaluate(self.bs, tenant_id, collection, timezone, version, candidate, now, ev), ev

    def due(self, tenant_id: str, now: float) -> bool:
        return engine.due(self.bs, tenant_id, now)

    def decision_state(self, tenant_id: str, band: int, now: float) -> tuple[str, Optional[str]]:
        return engine.decision_state(self.bs, tenant_id, band, now)

    # reading a band
    def band(self, tenant_id: str, band: int) -> Optional[dict]:
        return engine.band_snapshot(self.bs, tenant_id, band)

    def candidate(self, tenant_id: str, band: int) -> Optional[dict]:
        return engine.candidate_snapshot(self.bs, tenant_id, band)

    # moving a band
    def move_to_model(self, tenant_id, band, version, switched_week, now) -> Optional[str]:
        return engine.move_to_model(self.bs, tenant_id, band, version, switched_week, now)

    def approve_candidate(self, tenant_id, band, version, approved_by, now) -> Optional[str]:
        return engine.approve_candidate(self.bs, tenant_id, band, version, approved_by, now)

    def fallback(self, tenant_id, band, reason, now, actor=engine.ACTOR):
        ev = Events()
        return engine.fallback(self.bs, tenant_id, band, reason, now, ev, actor), ev

    def reset_all(self, tenant_id: str) -> None:
        engine.reset_all(self.bs, tenant_id)

    # promotion
    def promotion_plan(self, tenant_ids: list[str], candidate: str) -> dict:
        return engine.promotion_plan(self.bs, tenant_ids, candidate)

    def promote(self, plan, candidate, timezones, operator, now) -> Events:
        ev = Events()
        engine.promote(self.bs, plan, candidate, timezones, operator, now, ev)
        return ev

    # rows and labels
    def add_row(self, fields: dict, now: float):
        ev = Events()
        return engine.add_row(self.bs, fields, now, ev), ev

    def apply_label(self, link: str, label: str, source: str, now: float):
        ev = Events()
        return engine.apply_label(self.bs, link, label, source, now, ev), ev

    def after_fraud_label(self, row, now: float) -> Events:
        ev = Events()
        engine.after_fraud_label(self.bs, row, now, ev)
        return ev

    def purge(self, now: float, retention: dict) -> dict:
        return engine.purge(self.bs, now, retention)


def for_session(s):
    """The band store for a core session: the remote service if AMFA_BANDS_URL is set, else the core's own database."""
    from amfa import config
    if getattr(config.get(), "bands_url", ""):
        from amfa.bands.remote import RemoteStore
        return RemoteStore(s)
    return LocalStore(s)
