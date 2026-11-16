"""Grading each amount band, and whether it has earned the model (docs/rbi-banded-data-collection.md, section 2b and phase 3).

Every night, for each bank with the band switch on, each band's anonymous learning rows are graded: on the same payments, how
often did the rules and the model each catch fraud, and how often did each ask an honest customer for more than they needed?
A band whose model has done at least as well as the rules, on enough real evidence, for enough weeks running, becomes ELIGIBLE.

This module never moves a band to MODEL and never changes a decision. Moving a band to MODEL needs two people (phase 4).

What is graded:
  window   the latest max(8, minWeeks) weeks whose every payment is at least 30 days old (a fraud report or chargeback can arrive
           that late), so a payment is never counted as genuine before it had the chance to be reported
  fraud    a row labelled FRAUD by the bank or by the four-eyes review; whatever its outcome
  genuine  a row labelled GENUINE by the same, or a completed payment that nobody reported
  caught   asked for one biometric or more, or held (T2 and above); the same scale for rules and model
  ignored  declined, cancelled or expired payments that nobody labelled: nobody knows what they were
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from amfa.core import audit, db
from amfa.core.learning_store import LABEL_SOURCES
from amfa.engine.policy import BandGate, TenantPolicy
from amfa.engine.risk.collection_bands import BANDS, TOP, label

COLLECTING, ELIGIBLE, MODEL = "COLLECTING", "ELIGIBLE", "MODEL"
CAUGHT = {"T2", "T3", "T4"}
MATURE_DAYS = 30
MIN_WINDOW_WEEKS = 8
SMALL = 10                  # a figure built from fewer rows than this is never shown
DUE_AFTER_S = 20 * 3600     # nightly, give or take
ACTOR = "system:band-gate"


@dataclass
class Grade:
    genuine: int = 0
    fraud: int = 0
    weeks: int = 0
    model_caught: int = 0          # fraud the model caught
    rules_caught: int = 0          # fraud the rules caught
    model_only_missed: int = 0     # fraud the rules caught and the model let through lightly
    rules_only_missed: int = 0     # fraud the model caught and the rules let through lightly
    model_false: int = 0           # genuine payments the model asked a biometric or more of
    rules_false: int = 0
    week_list: list = field(default_factory=list)


def is_fraud(r) -> bool:
    return r.label == "FRAUD" and r.label_source in LABEL_SOURCES


def is_genuine(r) -> bool:
    if r.label == "GENUINE":
        return r.label_source in LABEL_SOURCES
    return r.label == "UNKNOWN" and r.outcome == "COMPLETED"


def grade(rows: Iterable) -> Grade:
    """Rows of one band, already limited to the window. Each needs label, label_source, outcome, rules_level, model_level, week_start."""
    g, weeks = Grade(), set()
    for r in rows:
        m, ru = r.model_level in CAUGHT, r.rules_level in CAUGHT
        if is_fraud(r):
            g.fraud += 1
            g.model_caught += m
            g.rules_caught += ru
            g.model_only_missed += ru and not m
            g.rules_only_missed += m and not ru
        elif is_genuine(r):
            g.genuine += 1
            g.model_false += m
            g.rules_false += ru
        else:
            continue
        weeks.add(r.week_start)
    g.weeks, g.week_list = len(weeks), sorted(weeks)
    return g


def passes(g: Grade, gate: BandGate, top: bool) -> tuple[bool, list[str]]:
    """Every gate of section 2b, with what is missing in plain words."""
    why = []
    if g.genuine < gate.minGenuine:
        why.append(f"needs {gate.minGenuine} genuine payments")
    if g.fraud < gate.minFraud:
        why.append(f"needs {gate.minFraud} real fraud reports")
    if g.weeks < gate.minWeeks:
        why.append(f"needs {gate.minWeeks} separate weeks of data")
    if g.model_only_missed > g.rules_only_missed:
        why.append("the model missed more fraud than the rules")
    if top and g.model_only_missed > 0:
        why.append("the model let through a fraud the rules would have stopped")
    if g.model_false > g.rules_false:
        why.append("the model asked more honest customers for extra checks than the rules")
    return not why, why


def window(now: float, tz: ZoneInfo, weeks: int) -> tuple[str, str]:
    """First and last week (their Mondays) whose every payment is at least MATURE_DAYS old."""
    newest_mature_day = datetime.fromtimestamp(now, tz).date() - timedelta(days=MATURE_DAYS + 7)
    last = newest_mature_day - timedelta(days=newest_mature_day.weekday())
    first = last - timedelta(weeks=weeks - 1)
    return first.isoformat(), last.isoformat()


def this_week(now: float, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(now, tz).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _state(s, tenant_id: str, band: int) -> db.BandGateState:
    row = s.get(db.BandGateState, (tenant_id, band))
    if row is None:
        row = db.BandGateState(tenant_id=tenant_id, band=band, state=COLLECTING, streak=0, figures={}, changed_at=time.time())
        s.add(row)
        s.flush()
    return row


def _figures(g: Grade, ok: bool, why: list[str], first: str, last: str) -> dict:
    out = {k: v for k, v in asdict(g).items() if k != "week_list"}
    return out | {"passes": ok, "missing": why, "windowFrom": first, "windowTo": last}


def evaluate(s, tenant_id: str, policy: TenantPolicy, now: Optional[float] = None) -> dict[int, db.BandGateState]:
    """Grade every band of one bank and move each between COLLECTING and ELIGIBLE. A band on MODEL is graded but not moved here."""
    now = now or time.time()
    tz = ZoneInfo(policy.timezone)
    c = policy.collection
    out = {}
    for band in BANDS:
        gate = c.topGate if band == TOP else c.gate
        first, last = window(now, tz, max(MIN_WINDOW_WEEKS, gate.minWeeks))
        rows = s.execute(select(db.LearningRow).where(
            db.LearningRow.tenant_id == tenant_id, db.LearningRow.band == band,
            db.LearningRow.week_start >= first, db.LearningRow.week_start <= last)).scalars().all()
        g = grade(rows)
        ok, why = passes(g, gate, band == TOP)
        st = _state(s, tenant_id, band)
        week = this_week(now, tz)
        if ok:
            if st.counted_week != week:              # one passing grading counts per week, however often it runs
                st.streak, st.counted_week = (st.streak or 0) + 1, week
        else:
            st.streak, st.counted_week = 0, None
        before = st.state
        if before == COLLECTING and ok and st.streak >= gate.passingEvaluations:
            st.state = ELIGIBLE
        elif before == ELIGIBLE and not ok:
            st.state = COLLECTING
        switched = (st.figures or {}).get("switchedWeek")
        st.figures, st.evaluated_at = _figures(g, ok, why, first, last), now
        if switched and before == MODEL:
            st.figures["switchedWeek"] = switched
        if before == MODEL:
            _watch_model(s, st, g, week, now)
        if st.state != before and before != MODEL:
            st.changed_at = now
            audit.record(ACTOR, f"band_gate.{st.state.lower()}", f"{tenant_id}:{band}",
                         {"band": band, "from": before, "to": st.state, "streak": st.streak, "genuine": g.genuine,
                          "fraud": g.fraud, "weeks": g.weeks, "missing": why}, tenant_id, s=s)
        out[band] = st
    s.flush()
    return out


# ---- after the switch: watching the model, and falling back to the rules ------------------------------------------------------

FALLBACK_AFTER_LOSSES = 2           # bands 0-2: weekly gradings lost in a row
STALE_S = 48 * 3600                 # a MODEL band not graded for this long is decided by the rules again


def loses(g: Grade) -> bool:
    """On the same payments, the model did worse than the rules at catching fraud or at leaving honest customers alone."""
    return g.model_only_missed > g.rules_only_missed or g.model_false > g.rules_false


def _watch_model(s, st: db.BandGateState, g: Grade, week: str, now: float) -> None:
    if st.band == TOP and _top_breach(s, st):
        fallback(s, st.tenant_id, st.band, "the model let through a fraud in the ₹1 lakh+ band that the rules would have stopped", now)
        return
    if loses(g):
        if st.losing_week != week:                       # one lost grading counts per week, like the passes
            st.losing, st.losing_week = (st.losing or 0) + 1, week
    else:
        st.losing, st.losing_week = 0, None
    if st.band != TOP and (st.losing or 0) >= FALLBACK_AFTER_LOSSES:
        fallback(s, st.tenant_id, st.band, f"the model did worse than the rules in {st.losing} weekly gradings in a row", now)
    elif st.band == TOP and loses(g):
        fallback(s, st.tenant_id, st.band, "the model did worse than the rules in the ₹1 lakh+ band", now)


def _top_breach(s, st: db.BandGateState) -> bool:
    """Any fraud in the top band, decided by the model since it switched, that the model let through lightly where the rules would
    have asked for a biometric or held it. No waiting for maturity: a fraud report is enough."""
    tz_week = st.figures.get("switchedWeek") or "0000-00-00"
    rows = s.execute(select(db.LearningRow).where(
        db.LearningRow.tenant_id == st.tenant_id, db.LearningRow.band == TOP, db.LearningRow.mode == MODEL,
        db.LearningRow.label == "FRAUD", db.LearningRow.week_start >= tz_week)).scalars().all()
    return any(is_fraud(r) and r.model_level not in CAUGHT and r.rules_level in CAUGHT for r in rows)


def fallback(s, tenant_id: str, band: int, reason: str, now: Optional[float] = None, actor: str = ACTOR) -> bool:
    """Back to the rules, at once. Needs no approval because it only adds safety. Audited, and raised as a security event."""
    now = now or time.time()
    st = _state(s, tenant_id, band)
    if st.state == COLLECTING:
        return False
    before = st.state
    st.state, st.streak, st.counted_week, st.losing, st.losing_week, st.changed_at = COLLECTING, 0, None, 0, None, now
    for r in s.execute(select(db.BandSwitchRequest).where(
            db.BandSwitchRequest.tenant_id == tenant_id, db.BandSwitchRequest.kind == "BAND_TO_MODEL",
            db.BandSwitchRequest.band == band, db.BandSwitchRequest.status == "PENDING")).scalars():
        r.status = "CANCELLED"
    audit.record(actor, "band_gate.fallback", f"{tenant_id}:{band}", {"band": band, "from": before, "to": COLLECTING,
                                                                      "reason": reason}, tenant_id, s=s)
    s.add(db.SecurityEvent(tenant_id=tenant_id, severity="HIGH" if band == TOP else "MEDIUM", kind="BAND_SWITCH_FALLBACK",
                           message=f"{label(band)}: back to the rules ({reason})"[:300], at=now))
    s.flush()
    return True


def on_fraud_label(s, row: db.LearningRow) -> None:
    """Called when a row is labelled FRAUD. In the top band, one fraud the model let through lightly where the rules would not have
    sends the band back to the rules at once, without waiting for the night's grading."""
    if row.band != TOP or row.mode != MODEL or not is_fraud(row):
        return
    if row.model_level in CAUGHT or row.rules_level not in CAUGHT:
        return
    st = s.get(db.BandGateState, (row.tenant_id, TOP))
    if st is not None and st.state == MODEL:
        fallback(s, row.tenant_id, TOP, "the model let through a fraud in the ₹1 lakh+ band that the rules would have stopped")


def state_for(s, tenant_id: str, band: int, now: Optional[float] = None) -> str:
    """The state a payment in [band] is decided under. Fails closed: no row, an unreadable row, an unknown state, or a MODEL band
    that has not been graded for STALE_S, is COLLECTING (the rules set the minimum)."""
    now = now or time.time()
    try:
        st = s.get(db.BandGateState, (tenant_id, band))
        if st is None or st.state not in (COLLECTING, ELIGIBLE, MODEL):
            return COLLECTING
        if st.state == MODEL:
            fresh = st.evaluated_at is not None and 0 <= now - st.evaluated_at <= STALE_S
            return MODEL if fresh else COLLECTING
        return st.state
    except Exception:
        return COLLECTING


# ---- changes that need two people ------------------------------------------------------------------------------------------

REQUEST_TTL_S = 72 * 3600


class GateError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


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


def request_model(s, tenant_id: str, band: int, operator_id: str, now: Optional[float] = None) -> db.BandSwitchRequest:
    """First of two people: ask for an ELIGIBLE band to be decided by the model."""
    now = now or time.time()
    policy = _policy_of(s, tenant_id)
    if band not in BANDS:
        raise GateError("BAND_UNKNOWN", f"there is no band {band}", 404)
    if not policy.collection.enabled:
        raise GateError("SWITCH_OFF", "the band switch is off for this bank")
    st = s.get(db.BandGateState, (tenant_id, band))
    if st is None or st.state != ELIGIBLE:
        raise GateError("NOT_ELIGIBLE", f"{label(band)} is {st.state if st else COLLECTING}, not ELIGIBLE: it has not earned the model")
    return _pending(s, tenant_id, "BAND_TO_MODEL", band, now) or _request(s, tenant_id, "BAND_TO_MODEL", band, {}, operator_id, now)


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
        policy = _policy_of(s, r.tenant_id)
        st = s.get(db.BandGateState, (r.tenant_id, r.band))
        if not policy.collection.enabled or st is None or st.state != ELIGIBLE:
            r.status = "CANCELLED"
            raise GateError("NOT_ELIGIBLE", f"{label(r.band)} is no longer ELIGIBLE")
        st.state, st.changed_at, st.losing, st.losing_week = MODEL, now, 0, None
        st.figures = dict(st.figures or {}) | {"switchedWeek": this_week(now, ZoneInfo(policy.timezone))}
        audit.record(f"operator:{operator_id}", "band_gate.model", f"{r.tenant_id}:{r.band}",
                     {"band": r.band, "request": r.id, "requestedBy": r.requested_by, "approvedBy": operator_id}, r.tenant_id, s=s)
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


def revert(s, tenant_id: str, band: int, operator_id: str, now: Optional[float] = None) -> bool:
    """One person is enough to send a band back to the rules: it only adds safety."""
    if band not in BANDS:
        raise GateError("BAND_UNKNOWN", f"there is no band {band}", 404)
    return fallback(s, tenant_id, band, f"sent back by operator {operator_id}", now, actor=f"operator:{operator_id}")


def _policy_of(s, tenant_id: str) -> TenantPolicy:
    t = s.get(db.Tenant, tenant_id)
    if t is None:
        raise GateError("TENANT_NOT_FOUND", "no such tenant", 404)
    return TenantPolicy.model_validate(t.policy)


def _apply_collection(s, tenant_id: str, collection: dict, actor: str) -> None:
    """Write an approved collection section into the bank's policy, as a new policy version. Turning the switch off also sends every
    band back to COLLECTING, so turning it on again later never brings back an old approval."""
    t = s.get(db.Tenant, tenant_id)
    new = TenantPolicy.model_validate(t.policy | {"collection": collection})
    t.policy = new.model_dump(mode="json")
    t.policy_version += 1
    s.add(db.PolicyVersion(tenant_id=t.id, version=t.policy_version, policy=t.policy, created_by=actor[:80]))
    if not new.collection.enabled:
        for st in s.execute(select(db.BandGateState).where(db.BandGateState.tenant_id == tenant_id)).scalars():
            st.state, st.streak, st.counted_week, st.losing, st.losing_week = COLLECTING, 0, None, 0, None
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


def run_due(s, now: Optional[float] = None) -> int:
    """The sweeper's nightly call: grade every bank that has the switch on and was not graded in the last ~20 hours."""
    now = now or time.time()
    n = 0
    for t in s.execute(select(db.Tenant)).scalars().all():
        policy = TenantPolicy.model_validate(t.policy)
        if not policy.collection.enabled:
            continue
        times = s.execute(select(db.BandGateState.evaluated_at).where(db.BandGateState.tenant_id == t.id)).scalars().all()
        if len(times) == len(BANDS) and all(x is not None and now - x < DUE_AFTER_S for x in times):
            continue
        evaluate(s, t.id, policy, now)
        n += 1
    return n


def _shown(n: int):
    return n if n >= SMALL else f"<{SMALL}"


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole >= SMALL else None


def view(s, tenant_id: str, policy: TenantPolicy) -> list[dict]:
    """Per band, for the admin console: state and figures, with every count under SMALL shown as "<10" and no rate built on one."""
    out = []
    for band in BANDS:
        gate = policy.collection.topGate if band == TOP else policy.collection.gate
        st = s.get(db.BandGateState, (tenant_id, band))
        f = (st.figures if st else None) or {}
        genuine, fraud = f.get("genuine", 0), f.get("fraud", 0)
        out.append({
            "band": band, "label": label(band), "state": st.state if st else COLLECTING,
            "streak": st.streak if st else 0, "needs": gate.model_dump(),
            "evaluatedAt": int(st.evaluated_at) if st and st.evaluated_at else None,
            "genuine": _shown(genuine), "fraud": _shown(fraud), "weeks": f.get("weeks", 0),
            "fraudCaught": {"model": _rate(f.get("model_caught", 0), fraud), "rules": _rate(f.get("rules_caught", 0), fraud)},
            "falseAlarms": {"model": _rate(f.get("model_false", 0), genuine), "rules": _rate(f.get("rules_false", 0), genuine)},
            "passes": bool(f.get("passes")), "missing": f.get("missing", []),
            "window": [f.get("windowFrom"), f.get("windowTo")]})
    return out
