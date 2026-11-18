"""The band switch's own logic: everything that reads or writes only band data (anonymous rows, band states, a candidate's
progress). It runs inside the core by default, or in the band service with its own database (amfa/bands/service.py).

It never touches the core's data. What the core must record (audit entries, security events, two-person requests to cancel) is
returned as events, and the core writes them into its own audit log and tables (amfa/core/band_gate.py). It never receives a
customer, an amount or a payment id: rows arrive already anonymous, and a label arrives by the row's keyed-hash link, whose key stays
in the core.

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

from sqlalchemy import delete, or_, select

from amfa.bands.models import BandCandidate, BandGateState, LearningRow
from amfa.engine.policy import BandGate
from amfa.engine.risk.collection_bands import BANDS, TOP, label

COLLECTING, ELIGIBLE, MODEL, APPROVED = "COLLECTING", "ELIGIBLE", "MODEL", "APPROVED"
CAUGHT = {"T2", "T3", "T4"}
LEVELS = ("T0", "T1", "T2", "T3", "T4")
MATURE_DAYS = 30
MIN_WINDOW_WEEKS = 8
SMALL = 10                          # a figure built from fewer rows than this is never shown
DUE_AFTER_S = 20 * 3600             # nightly, give or take
FALLBACK_AFTER_LOSSES = 2           # bands 0-2: weekly gradings lost in a row
STALE_S = 48 * 3600                 # a MODEL band not graded for this long is decided by the rules again
ACTOR = "system:band-gate"
# who may label a row: the bank (through its scoped API key) and the four-eyes review. Anything else is ignored.
LABEL_SOURCES = {"customer_report", "chargeback", "bank_ops", "review_verdict"}
LABELS = {"FRAUD", "GENUINE"}
# what a row may carry when it arrives: its categories, levels, outcome, label and link. Nothing else is accepted.
ROW_FIELDS = {"tenant_id", "band", "band_vs_usual", "payee_kind", "device_kind", "hour_bucket", "hard_sign", "rules_level",
              "model_level", "model_version", "candidate_level", "candidate_version", "mode", "outcome", "label", "label_source",
              "week_start", "link", "link_expires_at"}


class Events(list):
    """What the core must record because of a band change. Kept in order, so the audit log reads as it happened."""

    def audit(self, actor: str, action: str, target: str, detail: dict, tenant: Optional[str]) -> None:
        self.append({"kind": "audit", "actor": actor, "action": action, "target": target, "detail": detail, "tenant": tenant})

    def security(self, tenant: str, severity: str, event: str, message: str, at: float) -> None:
        self.append({"kind": "security", "tenant": tenant, "severity": severity, "event": event, "message": message[:300], "at": at})

    def cancel(self, tenant: Optional[str], band: Optional[int], kinds: tuple[str, ...]) -> None:
        """Pending two-person requests of these kinds (for this bank and band; None: all) are void."""
        self.append({"kind": "cancel_requests", "tenant": tenant, "band": band, "kinds": list(kinds)})


# ---- grading ---------------------------------------------------------------------------------------------------------------

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


def level_for(r, version: Optional[str]) -> Optional[str]:
    """What [version] asked for on this row: as the deciding model (model_level) or as the watching candidate (candidate_level).
    The same version is graded on both, so a promoted candidate keeps the evidence it built while it watched. With no version
    given, the row's deciding model."""
    if version is None or r.model_version == version:
        return r.model_level
    if getattr(r, "candidate_version", None) == version:
        return r.candidate_level
    return None


def grade(rows: Iterable, version: Optional[str] = None) -> Grade:
    """Rows of one band, already limited to the window, graded for [version] (see level_for). Each needs label, label_source,
    outcome, rules_level, the level fields and week_start."""
    g, weeks = Grade(), set()
    for r in rows:
        lvl = level_for(r, version)
        if lvl is None:
            continue
        m, ru = lvl in CAUGHT, r.rules_level in CAUGHT
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


def loses(g: Grade) -> bool:
    """On the same payments, the model did worse than the rules at catching fraud or at leaving honest customers alone."""
    return g.model_only_missed > g.rules_only_missed or g.model_false > g.rules_false


def window(now: float, tz: ZoneInfo, weeks: int) -> tuple[str, str]:
    """First and last week (their Mondays) whose every payment is at least MATURE_DAYS old."""
    newest_mature_day = datetime.fromtimestamp(now, tz).date() - timedelta(days=MATURE_DAYS + 7)
    last = newest_mature_day - timedelta(days=newest_mature_day.weekday())
    first = last - timedelta(weeks=weeks - 1)
    return first.isoformat(), last.isoformat()


def this_week(now: float, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(now, tz).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _figures(g: Grade, ok: bool, why: list[str], first: str, last: str) -> dict:
    out = {k: v for k, v in asdict(g).items() if k != "week_list"}
    return out | {"passes": ok, "missing": why, "windowFrom": first, "windowTo": last}


def _state(bs, tenant_id: str, band: int) -> BandGateState:
    row = bs.get(BandGateState, (tenant_id, band))
    if row is None:
        row = BandGateState(tenant_id=tenant_id, band=band, state=COLLECTING, streak=0, figures={}, changed_at=time.time())
        bs.add(row)
        bs.flush()
    return row


def evaluate(bs, tenant_id: str, collection, timezone: str, version: Optional[str], candidate: Optional[str],
             now: float, ev: Events) -> dict[int, BandGateState]:
    """Grade every band of one bank against [version] (the risk model deciding now) and move each between COLLECTING and ELIGIBLE;
    also grade [candidate] if one is installed. [collection] is the bank's collection policy (its gates).

    Only rows that version scored count: a new version starts from no evidence. A band ELIGIBLE or on MODEL for another version
    goes back to COLLECTING first (model_changed). With no version (no loadable model) nothing is graded and nothing moves; a band
    on MODEL is then decided by the rules once its last grading is older than STALE_S (decision_state)."""
    if version is None:
        return {}
    tz = ZoneInfo(timezone)
    cand = candidate if candidate != version else None
    out = {}
    for band in BANDS:
        gate = collection.topGate if band == TOP else collection.gate
        st = _state(bs, tenant_id, band)
        if st.state in (ELIGIBLE, MODEL) and st.model_version != version:
            model_changed(bs, st, version, now, ev)
        first, last = window(now, tz, max(MIN_WINDOW_WEEKS, gate.minWeeks))
        rows = bs.execute(select(LearningRow).where(
            LearningRow.tenant_id == tenant_id, LearningRow.band == band,
            or_(LearningRow.model_version == version, LearningRow.candidate_version == version),
            LearningRow.week_start >= first, LearningRow.week_start <= last)).scalars().all()
        g = grade(rows, version)
        ok, why = passes(g, gate, band == TOP)
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
        if st.state != MODEL:            # the version this grading ran under; a band on MODEL keeps the version it was approved on
            st.model_version = version
        if switched and before == MODEL:
            st.figures["switchedWeek"] = switched
        if before == MODEL:
            _watch_model(bs, st, g, week, now, ev)
        if st.state != before and before != MODEL:
            st.changed_at = now
            ev.audit(ACTOR, f"band_gate.{st.state.lower()}", f"{tenant_id}:{band}",
                     {"band": band, "from": before, "to": st.state, "streak": st.streak, "genuine": g.genuine,
                      "fraud": g.fraud, "weeks": g.weeks, "missing": why}, tenant_id)
        out[band] = st
        _grade_candidate(bs, tenant_id, band, gate, cand, tz, now, ev)
    bs.flush()
    return out


def due(bs, tenant_id: str, now: float) -> bool:
    """Has this bank not been graded in the last ~20 hours (or never)?"""
    times = bs.execute(select(BandGateState.evaluated_at).where(BandGateState.tenant_id == tenant_id)).scalars().all()
    return not (len(times) == len(BANDS) and all(x is not None and now - x < DUE_AFTER_S for x in times))


# ---- a candidate model earns each band while it watches ------------------------------------------------------------------------

def _candidate(bs, tenant_id: str, band: int, version: str, now: float, ev: Events) -> BandCandidate:
    """This band's progress for [version]. A different candidate's progress, and any approval it had, goes."""
    bc = bs.get(BandCandidate, (tenant_id, band))
    if bc is not None and bc.version != version:
        ev.audit(ACTOR, "band_candidate.replaced", f"{tenant_id}:{band}",
                 {"band": band, "from": bc.version, "to": version, "state": bc.state}, tenant_id)
        bs.delete(bc)
        bs.flush()
        bc = None
    if bc is None:
        bc = BandCandidate(tenant_id=tenant_id, band=band, version=version, state=COLLECTING, streak=0, figures={}, changed_at=now)
        bs.add(bc)
        bs.flush()
    return bc


def _grade_candidate(bs, tenant_id: str, band: int, gate: BandGate, version: Optional[str], tz: ZoneInfo, now: float,
                     ev: Events) -> None:
    """Grade the candidate in this band exactly as the deciding model is graded: its own answers on the same payments, against
    the rules, the same gates, the same weekly streak. It becomes ELIGIBLE here; two people make it APPROVED. One that starts
    losing goes back to COLLECTING and loses any approval. Nothing here touches the band's own state."""
    if version is None:
        bs.execute(delete(BandCandidate).where(BandCandidate.tenant_id == tenant_id, BandCandidate.band == band))
        return
    bc = _candidate(bs, tenant_id, band, version, now, ev)
    first, last = window(now, tz, max(MIN_WINDOW_WEEKS, gate.minWeeks))
    rows = bs.execute(select(LearningRow).where(
        LearningRow.tenant_id == tenant_id, LearningRow.band == band, LearningRow.candidate_version == version,
        LearningRow.week_start >= first, LearningRow.week_start <= last)).scalars().all()
    g = grade(rows, version)
    ok, why = passes(g, gate, band == TOP)
    week = this_week(now, tz)
    if ok:
        if bc.counted_week != week:
            bc.streak, bc.counted_week = (bc.streak or 0) + 1, week
    else:
        bc.streak, bc.counted_week = 0, None
    before = bc.state
    if before == COLLECTING and ok and bc.streak >= gate.passingEvaluations:
        bc.state = ELIGIBLE
    elif before in (ELIGIBLE, APPROVED) and not ok:
        bc.state, bc.approved_by, bc.approved_at = COLLECTING, None, None
    bc.figures, bc.evaluated_at = _figures(g, ok, why, first, last), now
    if bc.state != before:
        bc.changed_at = now
        ev.audit(ACTOR, f"band_candidate.{bc.state.lower()}", f"{tenant_id}:{band}",
                 {"band": band, "version": version, "from": before, "to": bc.state, "streak": bc.streak, "missing": why}, tenant_id)


def approve_candidate(bs, tenant_id: str, band: int, version: str, approved_by: str, now: float) -> Optional[str]:
    """Trust the candidate [version] in this band. Re-checked here, so nothing between the request and now can slip through: the
    band's candidate must still be [version] and ELIGIBLE. Returns an error code, or None when done."""
    bc = bs.get(BandCandidate, (tenant_id, band))
    if bc is None or bc.version != version:
        return "MODEL_CHANGED"
    if bc.state != ELIGIBLE:
        return "NOT_ELIGIBLE"
    bc.state, bc.approved_by, bc.approved_at, bc.changed_at = APPROVED, approved_by[:90], now, now
    bs.flush()
    return None


# ---- after the switch: watching the model, and falling back to the rules ------------------------------------------------------

def _watch_model(bs, st: BandGateState, g: Grade, week: str, now: float, ev: Events) -> None:
    if st.band == TOP and _top_breach(bs, st):
        fallback(bs, st.tenant_id, st.band, "the model let through a fraud in the ₹1 lakh+ band that the rules would have stopped",
                 now, ev)
        return
    if loses(g):
        if st.losing_week != week:                       # one lost grading counts per week, like the passes
            st.losing, st.losing_week = (st.losing or 0) + 1, week
    else:
        st.losing, st.losing_week = 0, None
    if st.band != TOP and (st.losing or 0) >= FALLBACK_AFTER_LOSSES:
        fallback(bs, st.tenant_id, st.band, f"the model did worse than the rules in {st.losing} weekly gradings in a row", now, ev)
    elif st.band == TOP and loses(g):
        fallback(bs, st.tenant_id, st.band, "the model did worse than the rules in the ₹1 lakh+ band", now, ev)


def _top_breach(bs, st: BandGateState) -> bool:
    """Any fraud in the top band, decided by the model since it switched, that the model let through lightly where the rules would
    have asked for a biometric or held it. No waiting for maturity: a fraud report is enough."""
    since = (st.figures or {}).get("switchedWeek") or "0000-00-00"
    rows = bs.execute(select(LearningRow).where(
        LearningRow.tenant_id == st.tenant_id, LearningRow.band == TOP, LearningRow.mode == MODEL,
        LearningRow.label == "FRAUD", LearningRow.model_version == st.model_version,
        LearningRow.week_start >= since)).scalars().all()
    return any(is_fraud(r) and r.model_level not in CAUGHT and r.rules_level in CAUGHT for r in rows)


def fallback(bs, tenant_id: str, band: int, reason: str, now: float, ev: Events, actor: str = ACTOR) -> bool:
    """Back to the rules, at once. Needs no approval because it only adds safety. Audited, and raised as a security event."""
    st = _state(bs, tenant_id, band)
    if st.state == COLLECTING:
        return False
    before = st.state
    st.state, st.streak, st.counted_week, st.losing, st.losing_week, st.changed_at = COLLECTING, 0, None, 0, None, now
    ev.cancel(tenant_id, band, ("BAND_TO_MODEL",))
    ev.audit(actor, "band_gate.fallback", f"{tenant_id}:{band}", {"band": band, "from": before, "to": COLLECTING, "reason": reason},
             tenant_id)
    ev.security(tenant_id, "HIGH" if band == TOP else "MEDIUM", "BAND_SWITCH_FALLBACK", f"{label(band)}: back to the rules ({reason})",
                now)
    bs.flush()
    return True


def model_changed(bs, st: BandGateState, version: str, now: float, ev: Events) -> None:
    """The risk model deciding now is not the one this band was graded or approved on. Whatever it earned belonged to that
    version: back to COLLECTING, its streak and any pending request gone. Approving it again needs a fresh pass and two people,
    even if the old version comes back later. Audited; raised as a security event when the band was on MODEL."""
    before, old = st.state, st.model_version
    st.state, st.streak, st.counted_week, st.losing, st.losing_week, st.changed_at = COLLECTING, 0, None, 0, None, now
    st.model_version = version
    ev.cancel(st.tenant_id, st.band, ("BAND_TO_MODEL",))
    ev.audit(ACTOR, "band_gate.model_changed", f"{st.tenant_id}:{st.band}",
             {"band": st.band, "from": before, "to": COLLECTING, "fromVersion": old, "toVersion": version}, st.tenant_id)
    if before == MODEL:
        ev.security(st.tenant_id, "HIGH" if st.band == TOP else "MEDIUM", "BAND_MODEL_CHANGED",
                    f"{label(st.band)}: the risk model changed from {old} to {version}; back to the rules until the new version "
                    f"earns it", now)
    bs.flush()


def after_fraud_label(bs, row: LearningRow, now: float, ev: Events) -> None:
    """A row was just labelled FRAUD. In the top band, one fraud the model let through lightly where the rules would not have sends
    the band back to the rules at once, without waiting for the night's grading."""
    if row.band != TOP or row.mode != MODEL or not is_fraud(row):
        return
    if row.model_level in CAUGHT or row.rules_level not in CAUGHT:
        return
    st = bs.get(BandGateState, (row.tenant_id, TOP))
    if st is not None and st.state == MODEL and row.model_version == st.model_version:
        fallback(bs, row.tenant_id, TOP, "the model let through a fraud in the ₹1 lakh+ band that the rules would have stopped",
                 now, ev)


def decision_state(bs, tenant_id: str, band: int, now: float) -> tuple[str, Optional[str]]:
    """The state a payment in [band] is decided under, and for MODEL the model version it was approved on. Fails closed: no row,
    an unreadable row, an unknown state, a MODEL band with no approved version or not graded for STALE_S, is COLLECTING (the rules
    set the minimum). The decision then also checks that the version that scored the payment is the approved one."""
    try:
        st = bs.get(BandGateState, (tenant_id, band))
        if st is None or st.state not in (COLLECTING, ELIGIBLE, MODEL):
            return COLLECTING, None
        if st.state == MODEL:
            fresh = st.evaluated_at is not None and 0 <= now - st.evaluated_at <= STALE_S
            ok = fresh and isinstance(st.model_version, str) and st.model_version
            return (MODEL, st.model_version) if ok else (COLLECTING, None)
        return st.state, None
    except Exception:
        return COLLECTING, None


def move_to_model(bs, tenant_id: str, band: int, version: str, switched_week: str, now: float) -> Optional[str]:
    """Put an ELIGIBLE band on [version]. Re-checked here, so nothing between the two approvals and now can slip through: the band
    must still be ELIGIBLE and graded on [version]. Returns an error code, or None when done."""
    st = bs.get(BandGateState, (tenant_id, band))
    if st is None or st.model_version != version:
        return "MODEL_CHANGED"
    if st.state != ELIGIBLE:
        return "NOT_ELIGIBLE"
    st.state, st.changed_at, st.losing, st.losing_week, st.model_version = MODEL, now, 0, None, version
    st.figures = dict(st.figures or {}) | {"switchedWeek": switched_week}
    bs.flush()
    return None


def reset_all(bs, tenant_id: str) -> None:
    """The bank turned the switch off: every band back to COLLECTING, so turning it on again never brings back an old approval."""
    for st in bs.execute(select(BandGateState).where(BandGateState.tenant_id == tenant_id)).scalars():
        st.state, st.streak, st.counted_week, st.losing, st.losing_week = COLLECTING, 0, None, 0, None
    bs.flush()


def band_snapshot(bs, tenant_id: str, band: int) -> Optional[dict]:
    st = bs.get(BandGateState, (tenant_id, band))
    if st is None:
        return None
    return {"state": st.state, "model_version": st.model_version, "streak": st.streak, "counted_week": st.counted_week,
            "evaluated_at": st.evaluated_at, "figures": dict(st.figures or {})}


def candidate_snapshot(bs, tenant_id: str, band: int) -> Optional[dict]:
    bc = bs.get(BandCandidate, (tenant_id, band))
    if bc is None:
        return None
    return {"version": bc.version, "state": bc.state, "streak": bc.streak, "counted_week": bc.counted_week,
            "evaluated_at": bc.evaluated_at, "figures": dict(bc.figures or {}), "approved_by": bc.approved_by}


# ---- promotion: the candidate's progress becomes the bands' own -------------------------------------------------------------------

def promotion_plan(bs, tenant_ids: list[str], candidate: str) -> dict[str, list[dict]]:
    """For every bank in [tenant_ids] (those with the switch on) and every band, what promoting [candidate] will do:
      MODEL       the candidate was approved here by two people: the band is decided by it, with no gap
      ELIGIBLE    it has earned the band but nobody approved it yet: it stays eligible, now for the new version
      COLLECTING  everything else; a band on the current model that the candidate has not earned is a drop"""
    out = {}
    for tenant_id in tenant_ids:
        rows = []
        for band in BANDS:
            st = bs.get(BandGateState, (tenant_id, band))
            bc = bs.get(BandCandidate, (tenant_id, band))
            now_state = st.state if st is not None else COLLECTING
            cand_state = bc.state if bc is not None and bc.version == candidate else COLLECTING
            after = MODEL if cand_state == APPROVED else ELIGIBLE if cand_state == ELIGIBLE else COLLECTING
            rows.append({"band": band, "label": label(band), "now": now_state, "nowVersion": st.model_version if st else None,
                         "candidate": cand_state, "after": after, "drop": now_state == MODEL and after != MODEL})
        out[tenant_id] = rows
    return out


def promote(bs, plan: dict[str, list[dict]], candidate: str, timezones: dict[str, str], operator: str, now: float,
            ev: Events) -> None:
    """Apply a promotion plan: every band takes its new state, tied to [candidate] and carrying the candidate's progress; then the
    candidate's progress is gone (it is the deciding model now). The core switches the model pointer only after this is stored."""
    for tenant_id, rows in plan.items():
        tz = ZoneInfo(timezones.get(tenant_id) or "UTC")
        for row in rows:
            st = _state(bs, tenant_id, row["band"])
            bc = bs.get(BandCandidate, (tenant_id, row["band"]))
            carried = bc is not None and bc.version == candidate
            before, old = st.state, st.model_version
            st.state, st.model_version, st.losing, st.losing_week, st.changed_at = row["after"], candidate, 0, None, now
            st.streak = bc.streak if carried else 0
            st.counted_week = bc.counted_week if carried else None
            if carried and bc.evaluated_at:
                st.evaluated_at = max(st.evaluated_at or 0, bc.evaluated_at)
            if row["after"] == MODEL:
                st.figures = dict(bc.figures or {}) | {"switchedWeek": this_week(now, tz)}
            ev.audit(f"operator:{operator}", "band_gate.promoted", f"{tenant_id}:{row['band']}",
                     {"band": row["band"], "from": before, "to": st.state, "fromVersion": old, "toVersion": candidate,
                      "approvedBy": bc.approved_by if carried else None}, tenant_id)
            if row["drop"]:
                ev.security(tenant_id, "HIGH" if row["band"] == TOP else "MEDIUM", "BAND_MODEL_CHANGED",
                            f"{label(row['band'])}: the risk model was promoted to {candidate}, which had not earned this band; "
                            f"back to the rules", now)
    bs.execute(delete(BandCandidate))
    ev.cancel(None, None, ("CANDIDATE_FOR_BAND", "BAND_TO_MODEL"))
    bs.flush()


# ---- rows and labels ---------------------------------------------------------------------------------------------------------

def apply_to_row(row: LearningRow, label_: str, source: str) -> bool:
    """FRAUD sticks: a later GENUINE never clears it, so no report can make a fraud case vanish from the grading."""
    if label_ not in LABELS or source not in LABEL_SOURCES:
        return False
    if row.label == "FRAUD" and label_ != "FRAUD":
        return False
    row.label, row.label_source = label_, source
    return True


def add_row(bs, fields: dict, now: float, ev: Events) -> Optional[LearningRow]:
    """Store one anonymous row (fields as the core built them). Refuses a field outside ROW_FIELDS, and writes at most one row per
    link, whoever sends it twice. A row that arrives labelled FRAUD is checked like any fresh fraud label."""
    extra = set(fields) - ROW_FIELDS
    if extra:
        raise ValueError(f"not a band row field: {sorted(extra)}")
    link = fields.get("link")
    if link and bs.execute(select(LearningRow.id).where(LearningRow.link == link)).first() is not None:
        return None
    row = LearningRow(id=uuid.uuid4().hex, **fields)
    bs.add(row)
    bs.flush()
    if row.label == "FRAUD":
        after_fraud_label(bs, row, now, ev)
    return row


def apply_label(bs, link: str, label_: str, source: str, now: float, ev: Events) -> bool:
    """A label for the row with this link, if it exists and its link has not expired."""
    if label_ not in LABELS or source not in LABEL_SOURCES:
        return False
    row = bs.execute(select(LearningRow).where(LearningRow.link == link)).scalars().first()
    if row is None or row.link_expires_at is None or row.link_expires_at <= now:
        return False
    if not apply_to_row(row, label_, source):
        return False
    if row.label == "FRAUD":
        after_fraud_label(bs, row, now, ev)
    return True


def expire_links(bs, now: float) -> int:
    """Wipe every link whose time is up. Runs on every purge, and on its own in the band service, so links never outlive their 90
    days even if the core stops calling."""
    from sqlalchemy import update
    return bs.execute(update(LearningRow).where(LearningRow.link_expires_at <= now)
                      .values(link=None, link_expires_at=None)).rowcount or 0


def purge(bs, now: float, retention: dict[str, tuple[int, str]]) -> dict[str, int]:
    """Wipe expired links (always, whatever any bank's setting), then delete rows older than each bank's retention. [retention]
    maps a bank to (days kept, its time zone); a bank not in it (one that is gone) keeps nothing."""
    out = {"learning_link": expire_links(bs, now)}
    gone = 0
    for tenant_id in bs.execute(select(LearningRow.tenant_id).distinct()).scalars().all():
        keep, tz = retention.get(tenant_id, (0, "UTC"))
        cutoff = (datetime.fromtimestamp(now, ZoneInfo(tz)).date() - timedelta(days=keep)).isoformat()
        gone += bs.execute(delete(LearningRow).where(LearningRow.tenant_id == tenant_id,
                                                     LearningRow.week_start < cutoff)).rowcount or 0
    out["learning_row"] = gone
    return out
