"""The band switch, from the core's side (docs/rbi-banded-data-collection.md).

The band data itself (anonymous rows, band states, a candidate's progress) and the logic that only touches it live in
amfa/bands/engine.py, reached through a band store (amfa/bands/store.py): inside the core by default, or the band service when
AMFA_BANDS_URL is set. This module keeps everything that needs the core's own data:
  - the bank's policy, and the risk model store (which version decides, which one watches)
  - operators and the two-person requests, the policy guard
  - the audit log and security events: a band change comes back from the store as events, written here into the one audit chain

Every band decision still fails closed: no state, an unknown state, an unreachable store or a stale grading means the rules set the
minimum.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from amfa.bands import store as band_store
from amfa.bands.engine import (ACTOR, APPROVED, CAUGHT, COLLECTING, DUE_AFTER_S, ELIGIBLE, FALLBACK_AFTER_LOSSES,  # noqa: F401
                               LABEL_SOURCES, MATURE_DAYS, MIN_WINDOW_WEEKS, MODEL, SMALL, STALE_S, Events, Grade, grade,
                               is_fraud, is_genuine, level_for, loses, passes, this_week, window)
from amfa.core import audit, db
from amfa.engine.policy import TenantPolicy
from amfa.engine.risk import scorer
from amfa.engine.risk.collection_bands import BANDS, TOP, label

PROMOTER = "system:promotion"
REQUEST_TTL_S = 72 * 3600


class GateError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def active_version() -> Optional[str]:
    """The version of the risk model deciding right now, or None if none can be loaded."""
    try:
        return scorer.active().version
    except Exception:
        return None


def apply_events(s, events: list[dict]) -> None:
    """Write what a band change requires into the core: audit entries, security events, and two-person requests that are void."""
    for e in events or []:
        kind = e.get("kind")
        if kind == "audit":
            audit.record(e["actor"], e["action"], e["target"], e.get("detail") or {}, e.get("tenant"), s=s)
        elif kind == "security":
            s.add(db.SecurityEvent(tenant_id=e["tenant"], severity=e["severity"], kind=e["event"], message=e["message"][:300],
                                   at=e.get("at") or time.time()))
        elif kind == "cancel_requests":
            q = select(db.BandSwitchRequest).where(db.BandSwitchRequest.status == "PENDING",
                                                   db.BandSwitchRequest.kind.in_(tuple(e.get("kinds") or ())))
            if e.get("tenant"):
                q = q.where(db.BandSwitchRequest.tenant_id == e["tenant"])
            if e.get("band") is not None:
                q = q.where(db.BandSwitchRequest.band == e["band"])
            for r in s.execute(q).scalars():
                r.status = "CANCELLED"
    s.flush()


# ---- grading ---------------------------------------------------------------------------------------------------------------

def evaluate(s, tenant_id: str, policy: TenantPolicy, now: Optional[float] = None) -> dict:
    """Grade every band of one bank against the risk model deciding now (and the candidate, if one watches), and move each between
    COLLECTING and ELIGIBLE (amfa/bands/engine.evaluate). With no loadable model nothing is graded and nothing moves."""
    now = now or time.time()
    version = active_version()
    if version is None:
        return {}
    out, events = band_store.for_session(s).evaluate(tenant_id, policy.collection, policy.timezone, version,
                                                     scorer.candidate_version(), now)
    apply_events(s, events)
    return out


def run_due(s, now: Optional[float] = None) -> int:
    """The sweeper's nightly call: grade every bank that has the switch on and was not graded in the last ~20 hours."""
    now = now or time.time()
    store = band_store.for_session(s)
    n = 0
    for t in s.execute(select(db.Tenant)).scalars().all():
        policy = TenantPolicy.model_validate(t.policy)
        if not policy.collection.enabled or not store.due(t.id, now):
            continue
        evaluate(s, t.id, policy, now)
        n += 1
    return n


def decision_state(s, tenant_id: str, band: int, now: Optional[float] = None) -> tuple[str, Optional[str]]:
    """The state a payment in [band] is decided under, and for MODEL the version it was approved on. Fails closed: anything the
    store cannot answer, or answers oddly, is COLLECTING (the rules set the minimum)."""
    now = now or time.time()
    try:
        state, version = band_store.for_session(s).decision_state(tenant_id, band, now)
    except Exception:
        return COLLECTING, None
    if state == MODEL and isinstance(version, str) and version:
        return MODEL, version
    return (state, None) if state in (COLLECTING, ELIGIBLE) else (COLLECTING, None)


def state_for(s, tenant_id: str, band: int, now: Optional[float] = None) -> str:
    return decision_state(s, tenant_id, band, now)[0]


def fallback(s, tenant_id: str, band: int, reason: str, now: Optional[float] = None, actor: str = ACTOR) -> bool:
    """Back to the rules, at once. Needs no approval because it only adds safety. Audited, and raised as a security event."""
    changed, events = band_store.for_session(s).fallback(tenant_id, band, reason, now or time.time(), actor)
    apply_events(s, events)
    return changed


def on_fraud_label(s, row) -> None:
    """A row (in the core's own database) was labelled FRAUD: the top band's instant fallback check."""
    apply_events(s, band_store.for_session(s).after_fraud_label(row, time.time()))


# ---- changes that need two people ------------------------------------------------------------------------------------------

def relaxes(old, new) -> list[str]:
    """What in a change to the `collection` section asks for less safety: turning it off, asking any gate for less evidence, or
    letting one customer add more rows a day. Raising a value, turning it on or keeping rows for less time is not relaxing."""
    why = []
    if old.enabled and not new.enabled:
        why.append("turns the band switch off")
    for name in ("gate", "topGate"):
        a, b = getattr(old, name), getattr(new, name)
        for f in type(a).model_fields:
            if getattr(b, f) < getattr(a, f):
                why.append(f"lowers collection.{name}.{f}")
    if new.dailyRowsPerCustomer > old.dailyRowsPerCustomer:
        why.append("raises collection.dailyRowsPerCustomer")
    return why


def _pending(s, tenant_id: str, kind: str, band: Optional[int], now: float) -> Optional[db.BandSwitchRequest]:
    return s.execute(select(db.BandSwitchRequest).where(
        db.BandSwitchRequest.tenant_id == tenant_id, db.BandSwitchRequest.kind == kind, db.BandSwitchRequest.band == band,
        db.BandSwitchRequest.status == "PENDING", db.BandSwitchRequest.expires_at > now)).scalars().first()


def _request(s, tenant_id: str, kind: str, band: Optional[int], payload: dict, operator_id: str, now: float) -> db.BandSwitchRequest:
    r = db.BandSwitchRequest(id=f"bsr_{uuid.uuid4().hex[:20]}", tenant_id=tenant_id, kind=kind, band=band, payload=payload,
                             status="PENDING", requested_by=operator_id, requested_at=now, expires_at=now + REQUEST_TTL_S)
    s.add(r)
    audit.record(f"operator:{operator_id}", "band_switch.request", r.id,
                 {"kind": kind, "band": band, "payload": payload}, tenant_id, s=s)
    s.flush()
    return r


def _policy_of(s, tenant_id: str) -> TenantPolicy:
    t = s.get(db.Tenant, tenant_id)
    if t is None:
        raise GateError("TENANT_NOT_FOUND", "no such tenant", 404)
    return TenantPolicy.model_validate(t.policy)


def request_model(s, tenant_id: str, band: int, operator_id: str, now: Optional[float] = None) -> db.BandSwitchRequest:
    """First of two people: ask for an ELIGIBLE band to be decided by the model."""
    now = now or time.time()
    policy = _policy_of(s, tenant_id)
    if band not in BANDS:
        raise GateError("BAND_UNKNOWN", f"there is no band {band}", 404)
    if not policy.collection.enabled:
        raise GateError("SWITCH_OFF", "the band switch is off for this bank")
    st = band_store.for_session(s).band(tenant_id, band)
    if st is None or st["state"] != ELIGIBLE:
        raise GateError("NOT_ELIGIBLE", f"{label(band)} is {st['state'] if st else COLLECTING}, not ELIGIBLE: it has not earned "
                                        "the model")
    version = active_version()
    if version is None:
        raise GateError("NO_MODEL", "no risk model can be loaded, so there is nothing to approve")
    if st["model_version"] != version:
        raise GateError("MODEL_CHANGED", f"{label(band)} was graded on {st['model_version']}, but {version} decides now: it has "
                                         "not earned this version yet")
    pending = _pending(s, tenant_id, "BAND_TO_MODEL", band, now)
    if pending is not None and (pending.payload or {}).get("modelVersion") == version:
        return pending
    if pending is not None:                             # asked for under another version: void
        pending.status = "CANCELLED"
    return _request(s, tenant_id, "BAND_TO_MODEL", band, {"modelVersion": version}, operator_id, now)


def request_candidate(s, tenant_id: str, band: int, operator_id: str, now: Optional[float] = None) -> db.BandSwitchRequest:
    """First of two people: ask for the candidate to be trusted in this band once it is promoted."""
    now = now or time.time()
    policy = _policy_of(s, tenant_id)
    if band not in BANDS:
        raise GateError("BAND_UNKNOWN", f"there is no band {band}", 404)
    if not policy.collection.enabled:
        raise GateError("SWITCH_OFF", "the band switch is off for this bank")
    version = scorer.candidate_version()
    if version is None:
        raise GateError("NO_CANDIDATE", "no candidate model is installed")
    bc = band_store.for_session(s).candidate(tenant_id, band)
    if bc is None or bc["version"] != version or bc["state"] != ELIGIBLE:
        shown = bc["state"] if bc is not None and bc["version"] == version else COLLECTING
        raise GateError("NOT_ELIGIBLE", f"the candidate {version} has not earned {label(band)} ({shown})")
    pending = _pending(s, tenant_id, "CANDIDATE_FOR_BAND", band, now)
    if pending is not None and (pending.payload or {}).get("candidateVersion") == version:
        return pending
    if pending is not None:
        pending.status = "CANCELLED"
    return _request(s, tenant_id, "CANDIDATE_FOR_BAND", band, {"candidateVersion": version}, operator_id, now)


def request_policy(s, tenant_id: str, collection: dict, operator_id: str, now: Optional[float] = None) -> db.BandSwitchRequest:
    """First of two people: a change to the `collection` section that relaxes it. One that does not relax it needs no request."""
    from amfa.engine.policy import CollectionPolicy
    now = now or time.time()
    policy = _policy_of(s, tenant_id)
    try:
        new = CollectionPolicy.model_validate(collection)
        TenantPolicy.model_validate(policy.model_dump(mode="json") | {"collection": new.model_dump(mode="json")})
    except ValueError as exc:
        raise GateError("POLICY_INVALID", str(exc)[:500], 422) from None
    if not relaxes(policy.collection, new):
        raise GateError("NOT_RELAXING", "this change does not relax the band switch: save it as a normal policy change")
    # the section as it stands now goes with the request: if anything changes it before approval, the approver would be signing off
    # a different change from the one shown, so the request is void then
    return _request(s, tenant_id, "POLICY", None, {"collection": new.model_dump(mode="json"),
                                                   "base": policy.collection.model_dump(mode="json"),
                                                   "relaxes": relaxes(policy.collection, new)}, operator_id, now)


def approve(s, request_id: str, operator_id: str, now: Optional[float] = None) -> dict:
    """Second of two people. A different operator, before the request expires, and only if what it asks for is still allowed."""
    now = now or time.time()
    r = s.get(db.BandSwitchRequest, request_id)
    if r is None:
        raise GateError("REQUEST_NOT_FOUND", "no such request", 404)
    if r.status != "PENDING":
        raise GateError("REQUEST_CLOSED", f"this request is {r.status}")
    if r.expires_at <= now:
        r.status = "CANCELLED"
        raise GateError("REQUEST_EXPIRED", "this request expired; ask again")
    if r.requested_by == operator_id:
        raise GateError("FOUR_EYES", "a second, different operator must approve")
    if r.kind == "BAND_TO_MODEL":
        _approve_model(s, r, operator_id, now)
    elif r.kind == "CANDIDATE_FOR_BAND":
        _approve_candidate(s, r, operator_id, now)
    elif r.kind == "POLICY":
        current = _policy_of(s, r.tenant_id).collection.model_dump(mode="json")
        if current != r.payload.get("base"):
            r.status = "CANCELLED"
            s.flush()
            raise GateError("POLICY_CHANGED", "the band switch settings changed after this request: ask again")
        _apply_collection(s, r.tenant_id, r.payload["collection"], f"operator:{r.requested_by}+operator:{operator_id}")
    else:
        raise GateError("REQUEST_UNKNOWN", f"unknown request kind {r.kind}", 422)
    r.status, r.approved_by, r.approved_at = "APPROVED", operator_id, now
    s.flush()
    return {"id": r.id, "status": r.status, "kind": r.kind, "band": r.band}


def _approve_model(s, r: db.BandSwitchRequest, operator_id: str, now: float) -> None:
    policy = _policy_of(s, r.tenant_id)
    store = band_store.for_session(s)
    st = store.band(r.tenant_id, r.band)
    asked = (r.payload or {}).get("modelVersion")
    version = active_version()
    if not asked or version != asked or (st is not None and st["model_version"] != asked):
        r.status = "CANCELLED"
        s.flush()
        raise GateError("MODEL_CHANGED", f"this request was for {asked}, but {version} decides now: ask again once the band has "
                                         "earned the new version")
    if not policy.collection.enabled or st is None or st["state"] != ELIGIBLE:
        r.status = "CANCELLED"
        raise GateError("NOT_ELIGIBLE", f"{label(r.band)} is no longer ELIGIBLE")
    code = store.move_to_model(r.tenant_id, r.band, asked, this_week(now, ZoneInfo(policy.timezone)), now)
    if code:                                            # it changed between the check and the move: refused by the store itself
        r.status = "CANCELLED"
        s.flush()
        raise GateError(code, f"{label(r.band)} can no longer be moved to {asked}")
    audit.record(f"operator:{operator_id}", "band_gate.model", f"{r.tenant_id}:{r.band}",
                 {"band": r.band, "request": r.id, "requestedBy": r.requested_by, "approvedBy": operator_id,
                  "modelVersion": asked}, r.tenant_id, s=s)


def _approve_candidate(s, r: db.BandSwitchRequest, operator_id: str, now: float) -> None:
    asked = (r.payload or {}).get("candidateVersion")
    version = scorer.candidate_version()
    store = band_store.for_session(s)
    bc = store.candidate(r.tenant_id, r.band)
    if not asked or version != asked or bc is None or bc["version"] != asked:
        r.status = "CANCELLED"
        s.flush()
        raise GateError("MODEL_CHANGED", f"this request was for the candidate {asked}, but the candidate now is {version}")
    if bc["state"] != ELIGIBLE:
        r.status = "CANCELLED"
        raise GateError("NOT_ELIGIBLE", f"the candidate is {bc['state']} in {label(r.band)}, not ELIGIBLE")
    code = store.approve_candidate(r.tenant_id, r.band, asked, f"{r.requested_by},{operator_id}", now)
    if code:
        r.status = "CANCELLED"
        s.flush()
        raise GateError(code, f"the candidate {asked} can no longer be trusted in {label(r.band)}")
    audit.record(f"operator:{operator_id}", "band_candidate.approved", f"{r.tenant_id}:{r.band}",
                 {"band": r.band, "request": r.id, "version": asked, "requestedBy": r.requested_by, "approvedBy": operator_id},
                 r.tenant_id, s=s)


def revert(s, tenant_id: str, band: int, operator_id: str, now: Optional[float] = None) -> bool:
    """One person is enough to send a band back to the rules: it only adds safety."""
    if band not in BANDS:
        raise GateError("BAND_UNKNOWN", f"there is no band {band}", 404)
    return fallback(s, tenant_id, band, f"sent back by operator {operator_id}", now, actor=f"operator:{operator_id}")


def _apply_collection(s, tenant_id: str, collection: dict, actor: str) -> None:
    """Write an approved collection section into the bank's policy, as a new policy version. Turning the switch off also sends every
    band back to COLLECTING, so turning it on again later never brings back an old approval."""
    t = s.get(db.Tenant, tenant_id)
    new = TenantPolicy.model_validate(t.policy | {"collection": collection})
    t.policy = new.model_dump(mode="json")
    t.policy_version += 1
    s.add(db.PolicyVersion(tenant_id=t.id, version=t.policy_version, policy=t.policy, created_by=actor[:80]))
    if not new.collection.enabled:
        band_store.for_session(s).reset_all(tenant_id)
    audit.record(actor, "policy.update", tenant_id, {"version": t.policy_version, "changed": ["collection"], "twoPerson": True},
                 tenant_id, s=s)


def requests(s, tenant_id: str, now: Optional[float] = None) -> list[dict]:
    now = now or time.time()
    rows = s.execute(select(db.BandSwitchRequest).where(db.BandSwitchRequest.tenant_id == tenant_id)
                     .order_by(db.BandSwitchRequest.requested_at.desc()).limit(50)).scalars().all()
    return [{"id": r.id, "kind": r.kind, "band": r.band, "payload": r.payload,
             "status": "EXPIRED" if r.status == "PENDING" and r.expires_at <= now else r.status,
             "requestedBy": r.requested_by, "requestedAt": int(r.requested_at), "expiresAt": int(r.expires_at),
             "approvedBy": r.approved_by} for r in rows]


# ---- what the console shows ------------------------------------------------------------------------------------------------

def _shown(n: int):
    return n if n >= SMALL else f"<{SMALL}"


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole >= SMALL else None


def _candidate_view(store, tenant_id: str, band: int) -> Optional[dict]:
    bc = store.candidate(tenant_id, band)
    if bc is None or bc["version"] != scorer.candidate_version():
        return None
    f = bc["figures"] or {}
    genuine, fraud = f.get("genuine", 0), f.get("fraud", 0)
    return {"version": bc["version"], "state": bc["state"], "streak": bc["streak"], "genuine": _shown(genuine),
            "fraud": _shown(fraud), "fraudCaught": _rate(f.get("model_caught", 0), fraud),
            "falseAlarms": _rate(f.get("model_false", 0), genuine), "missing": f.get("missing", []),
            "approvedBy": bc["approved_by"]}


def view(s, tenant_id: str, policy: TenantPolicy) -> list[dict]:
    """Per band, for the admin console: state and figures, with every count under SMALL shown as "<10" and no rate built on one."""
    store = band_store.for_session(s)
    out = []
    for band in BANDS:
        gate = policy.collection.topGate if band == TOP else policy.collection.gate
        st = store.band(tenant_id, band)
        f = (st["figures"] if st else None) or {}
        genuine, fraud = f.get("genuine", 0), f.get("fraud", 0)
        out.append({
            "band": band, "label": label(band), "state": st["state"] if st else COLLECTING,
            "streak": st["streak"] if st else 0, "needs": gate.model_dump(), "modelVersion": st["model_version"] if st else None,
            "evaluatedAt": int(st["evaluated_at"]) if st and st["evaluated_at"] else None,
            "genuine": _shown(genuine), "fraud": _shown(fraud), "weeks": f.get("weeks", 0),
            "fraudCaught": {"model": _rate(f.get("model_caught", 0), fraud), "rules": _rate(f.get("rules_caught", 0), fraud)},
            "falseAlarms": {"model": _rate(f.get("model_false", 0), genuine), "rules": _rate(f.get("rules_false", 0), genuine)},
            "passes": bool(f.get("passes")), "missing": f.get("missing", []),
            "window": [f.get("windowFrom"), f.get("windowTo")], "candidate": _candidate_view(store, tenant_id, band)})
    return out


# ---- promotion: the candidate becomes the deciding model ---------------------------------------------------------------------

def _switched_on(s) -> list[db.Tenant]:
    return [t for t in s.execute(select(db.Tenant)).scalars().all() if TenantPolicy.model_validate(t.policy).collection.enabled]


def promotion_preview(s) -> dict:
    """What promoting the candidate will do, for every bank with the band switch on and every band (amfa/bands/engine.promotion_plan).
    A band on the current model that the candidate has not earned is a drop, listed in `drops`. Banks with the switch off get the
    new model as any model update gives it; the band switch does not govern them."""
    cand, active = scorer.candidate_version(), active_version()
    if cand is None:
        raise GateError("NO_CANDIDATE", "no candidate model is installed")
    tenants = _switched_on(s)
    plan = band_store.for_session(s).promotion_plan([t.id for t in tenants], cand)
    banks, drops = [], []
    for t in tenants:
        bands = plan.get(t.id, [])
        drops += [{"tenantId": t.id, "band": r["band"], "label": r["label"]} for r in bands if r["drop"]]
        banks.append({"tenantId": t.id, "name": t.name, "bands": bands})
    return {"active": active, "candidate": cand, "banks": banks, "drops": drops}


def promote(operator_id: str, confirm_drops: bool = False, now: Optional[float] = None) -> dict:
    """Make the candidate the deciding model, band by band as promotion_preview says. Refused while it would send a band on the
    current model back to the rules, unless [confirm_drops]. Band states are written and committed first, then the pointer is
    switched: if the second step never happens, the bands name a version that is not deciding, and every decision and the
    night's grading treat them as the rules. Fails closed."""
    now = now or time.time()
    with db.session() as s:
        preview = promotion_preview(s)
        if preview["drops"] and not confirm_drops:
            names = ", ".join(f"{d['tenantId']} {d['label']}" for d in preview["drops"])
            raise GateError("WOULD_DROP", f"promoting now sends these bands back to the rules: {names}. Wait until the candidate "
                                          "has earned them and been approved, or confirm the drops")
        cand = preview["candidate"]
        if scorer.candidate() is None:
            raise GateError("CANDIDATE_BROKEN", f"the candidate {cand} no longer loads; it cannot be promoted")
        plan = {b["tenantId"]: b["bands"] for b in preview["banks"]}
        timezones = {b["tenantId"]: _policy_of(s, b["tenantId"]).timezone for b in preview["banks"]}
        apply_events(s, band_store.for_session(s).promote(plan, cand, timezones, operator_id, now))
        audit.record(f"operator:{operator_id}", "risk.promote", cand,
                     {"from": preview["active"], "to": cand, "drops": preview["drops"]}, None, s=s)
    scorer.promote_candidate(cand)
    return preview
