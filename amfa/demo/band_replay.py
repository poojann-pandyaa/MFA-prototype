"""A simulated bank replayed through the band switch from its first day (docs/rbi-banded-data-collection.md, phase 5).

Every payment of the simulator's customers, in time order, goes through the real decision code (engine/decide.decide, with the
model, the reliefs, the floors and the band switch) under its band's state at that moment. Its anonymous learning row is written,
labelled as the bank would label it, and each simulated night the real grading (core/band_gate.evaluate) runs. When a band becomes
ELIGIBLE, two simulated operators approve it at once, the fastest a real bank could move. The replay records, per band, when that
happened and what it cost: how often honest customers met a face check or more, and how much fraud did, while collecting and after.

What is real here and what is not:
  real       decide(), the model, band_switch, the learning-row categories, band_gate's grading, gates, approvals and fallbacks
  simulated  the customers and their payments; the request context is built from the simulator's own features (the live context
             builder needs a bank's database); fraud labels arrive at the time of the payment as "chargeback" (a real bank's arrive
             days or weeks later, which only delays the switch); every payment completes
  left out   the daily row cap (simulated customers do not flood); learning-phase customers (the replay treats everyone as READY,
             which is where the switch acts)

With `label_source="simulator"` the fraud labels are written with a source the grading does not accept, which is what a bank
with no real fraud reports looks like: no band may ever become eligible then.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from amfa.core import band_gate, db
from amfa.engine import band_switch
from amfa.engine.context import (Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, Factor, Plan, Policy, Segment,
                                 TransactionCtx, TxnType)
from amfa.engine.decide import decide
from amfa.engine.policy import TenantPolicy
from amfa.engine.risk.collection_bands import BANDS, band_of
from amfa.core.learning_store import hour_bucket, week_start

IST = ZoneInfo("Asia/Kolkata")
DAY = 86_400.0
MONEY = {"TRANSFER", "PAYMENT", "CARD_NOT_PRESENT"}
ALL_FACTORS = Enrollment(otp=True, face=True, voice=True, keystroke=True, shape=True, keystrokeTouch=True, shapeTouch=True,
                         deviceKey=True)
ORDER = {Band.LOW: 0, Band.MEDIUM: 1, Band.HIGH: 2}


@dataclass
class BandStats:
    genuine: dict = field(default_factory=lambda: defaultdict(int))        # mode -> payments
    genuine_up: dict = field(default_factory=lambda: defaultdict(int))     # mode -> stepped up to a biometric or held
    fraud: dict = field(default_factory=lambda: defaultdict(int))
    fraud_caught: dict = field(default_factory=lambda: defaultdict(int))
    model_alone_fraud_caught: dict = field(default_factory=lambda: defaultdict(int))   # mode -> what the model's own call caught
    model_alone_genuine_up: dict = field(default_factory=lambda: defaultdict(int))
    eligible_day: Optional[int] = None
    model_day: Optional[int] = None
    fallback_days: list = field(default_factory=list)
    last_missing: list = field(default_factory=list)                      # what the last grading said was missing
    last_streak: int = 0                                                  # weekly passes in a row at the last grading
    candidate_approved_day: Optional[int] = None                          # two (simulated) people trusted the candidate here


@dataclass
class Report:
    days: int = 0
    payments: int = 0
    weaker_than_off: int = 0            # payments where the switch on asked for less than the switch off (must be 0)
    bands: dict = field(default_factory=lambda: {b: BandStats() for b in BANDS})
    timeline: list = field(default_factory=list)


def stepped_up(plan: Plan) -> bool:
    return band_switch.level_of(plan.required, plan.escalate == "MANUAL_REVIEW") in band_gate.CAUGHT


def _behaviour(risk: Optional[float]) -> Band:
    if risk is None:
        return Band.MEDIUM
    return Band.HIGH if risk >= 0.7 else Band.MEDIUM if risk >= 0.4 else Band.LOW


def context_of(row, policy: TenantPolicy, state: Optional[str], switch: bool = True, version: Optional[str] = None) -> Context:
    """A request context from a simulator row's own features, as close to the live context builder as the features allow."""
    f = row.features
    amount, exposure = int(round(row.amount * 100)), int(round(row.exposure * 100))
    ttype = TxnType(f.get("txn_type") or "TRANSFER")
    new_payee = not f.get("payee_seen")
    band, exp_band = policy.band_for(amount, "INR"), policy.band_for(exposure, "INR")
    segment = Segment(row.segment) if row.segment in Segment.__members__ else Segment.STANDARD
    floor, org = policy.derive(ttype, band, amount, "INR", segment, new_payee)
    return Context(
        userId=row.customer, transactionId=f"sim_{uuid.uuid4().hex[:10]}",
        transaction=TransactionCtx(amountBand=band, exposureBand=exp_band, exposureMinor=exposure, isNewBeneficiary=new_payee,
                                   type=ttype),
        device=DeviceCtx(deviceKnown=bool(f.get("device_seen")), locationKnown=bool(f.get("place_seen") or f.get("network_seen")),
                         hourUnusual=bool(f.get("is_night"))),
        behavioural=BehaviouralCtx(band=_behaviour(f.get("ueba_risk"))), enrollment=ALL_FACTORS, org=org,
        policy=Policy(actionFloor=floor, smallPaymentCap=policy.smallPaymentCap, lowTouch=policy.lowTouch, phase="READY",
                      bandSwitch=switch, bandState=state, bandModelVersion=version))


def not_weaker(a: Plan, b: Plan) -> bool:
    return (set(b.required) <= set(a.required) and ORDER[a.riskBand] >= ORDER[b.riskBand]
            and (b.escalate is None or a.escalate is not None))


def replay(rows: Iterable, *, tenant_id: Optional[str] = None, policy: Optional[TenantPolicy] = None, approve: bool = True,
           label_source: str = "chargeback", compare_off: bool = True, progress=None, on_day=None) -> Report:
    """Replay [rows] (simulator rows, any order) day by day. Writes learning rows and band states into the database for [tenant_id]
    (a throwaway bank is made if none is given). [on_day](day, report, tenant_id, day_start), if given, runs at the start of every
    day, before its payments: that is where a model update (installing a candidate, promoting it) happens in a replay."""
    db.migrate()
    policy = policy or TenantPolicy.model_validate({"collection": {"enabled": True}})
    if tenant_id is None:
        tenant_id = f"tn_replay_{uuid.uuid4().hex[:10]}"
        with db.session() as s:
            s.add(db.Tenant(id=tenant_id, name="Replay bank", slug=f"rp{uuid.uuid4().hex[:8]}", policy=policy.model_dump(mode="json")))
    pays = sorted((r for r in rows if (r.features.get("txn_type") or "TRANSFER") in MONEY and r.amount > 0), key=lambda r: r.at)
    rep = Report()
    if not pays:
        return rep
    start = pays[0].at
    day_of = lambda at: int((at - start) // DAY)
    i = 0
    last_day = day_of(pays[-1].at)
    for day in range(last_day + 1):
        end = start + (day + 1) * DAY
        if on_day is not None:
            on_day(day, rep, tenant_id, end - DAY)
        with db.session() as s:
            decided = {b: band_gate.decision_state(s, tenant_id, b, end - DAY / 2) for b in BANDS}
        states = {b: st for b, (st, _) in decided.items()}
        out = []
        while i < len(pays) and pays[i].at < end:
            r = pays[i]
            i += 1
            band = band_of(int(round(r.exposure * 100)))
            state = states[band]
            plan = decide(context_of(r, policy, state, version=decided[band][1]), features=r.features)
            st = rep.bands[band]
            mode = plan.bandState or band_switch.COLLECTING
            if compare_off:
                off = decide(context_of(r, policy, None, switch=False), features=r.features)
                rep.weaker_than_off += not not_weaker(plan, off)
            alone = plan.bandView["modelLevel"] in band_gate.CAUGHT
            if r.label:
                st.fraud[mode] += 1
                st.fraud_caught[mode] += stepped_up(plan)
                st.model_alone_fraud_caught[mode] += alone
            else:
                st.genuine[mode] += 1
                st.genuine_up[mode] += stepped_up(plan)
                st.model_alone_genuine_up[mode] += alone
            v = plan.bandView
            out.append(db.LearningRow(
                id=uuid.uuid4().hex, tenant_id=tenant_id, band=v["band"], band_vs_usual="SAME", payee_kind=v["payeeKind"],
                device_kind=v["deviceKind"], hour_bucket=hour_bucket(datetime.fromtimestamp(r.at, IST).hour),
                hard_sign=bool(v["hardSign"]), rules_level=v["rulesLevel"], model_level=v["modelLevel"],
                model_version=v.get("modelVersion"), candidate_level=v.get("candidateLevel"),
                candidate_version=v.get("candidateVersion"), mode=v["mode"],
                outcome="COMPLETED", label="FRAUD" if r.label else "UNKNOWN", label_source=label_source if r.label else None,
                week_start=week_start(r.at, IST)))
            rep.payments += 1
        with db.session() as s:
            s.add_all(out)
        with db.session() as s:                           # the night's grading, then (if asked) two people approve at once
            for b, gs in band_gate.evaluate(s, tenant_id, policy, end).items():
                before = states[b]
                rep.bands[b].last_missing = list((gs.figures or {}).get("missing", []))
                rep.bands[b].last_streak = gs.streak or 0
                if gs.state == band_gate.ELIGIBLE and rep.bands[b].eligible_day is None:
                    rep.bands[b].eligible_day = day
                if gs.state == band_gate.ELIGIBLE and approve:
                    req = band_gate.request_model(s, tenant_id, b, "sim_operator_1", end)
                    band_gate.approve(s, req.id, "sim_operator_2", end)
                    rep.bands[b].model_day = day if rep.bands[b].model_day is None else rep.bands[b].model_day
                if before == band_gate.MODEL and s.get(db.BandGateState, (tenant_id, b)).state == band_gate.COLLECTING:
                    rep.bands[b].fallback_days.append(day)
                bc = s.get(db.BandCandidate, (tenant_id, b))
                if approve and bc is not None and bc.state == band_gate.ELIGIBLE:      # the candidate earned it: two people agree
                    req = band_gate.request_candidate(s, tenant_id, b, "sim_operator_1", end)
                    band_gate.approve(s, req.id, "sim_operator_2", end)
                    if rep.bands[b].candidate_approved_day is None:
                        rep.bands[b].candidate_approved_day = day
            rep.timeline.append((day, {b: s.get(db.BandGateState, (tenant_id, b)).state for b in BANDS}))
        if progress and day % 10 == 0:
            progress(day, rep)
    rep.days = last_day + 1
    return rep


def rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def summary(rep: Report) -> list[dict]:
    out = []
    for b, st in rep.bands.items():
        g_all, f_all = sum(st.genuine.values()), sum(st.fraud.values())
        out.append({
            "band": b, "eligibleDay": st.eligible_day, "modelDay": st.model_day, "fallbackDays": st.fallback_days,
            "genuine": g_all, "fraud": f_all,
            "stepUpWhileCollecting": rate(st.genuine_up["COLLECTING"] + st.genuine_up["ELIGIBLE"],
                                          st.genuine["COLLECTING"] + st.genuine["ELIGIBLE"]),
            "stepUpOnModel": rate(st.genuine_up["MODEL"], st.genuine["MODEL"]),
            "fraudCaughtWhileCollecting": rate(st.fraud_caught["COLLECTING"] + st.fraud_caught["ELIGIBLE"],
                                               st.fraud["COLLECTING"] + st.fraud["ELIGIBLE"]),
            "fraudCaughtOnModel": rate(st.fraud_caught["MODEL"], st.fraud["MODEL"]),
            "modelAloneFraudCaught": rate(sum(st.model_alone_fraud_caught.values()), f_all),
            "modelAloneStepUp": rate(sum(st.model_alone_genuine_up.values()), g_all),
            "lastMissing": st.last_missing,
        })
    return out
