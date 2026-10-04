"""
Per-dimension scoring, shared by every endpoint.

Two families, scored differently on purpose:

  Challenge dimensions -- keystroke, shape. The user was asked to do
  something. An empty or partial answer is scored, blended toward a
  typical impostor by how much is missing. Never skipped.

  Passive dimensions -- session, transaction. Nothing was asked of the
  user. No history means nothing to compare against, so these report
  INSUFFICIENT_DATA and are skipped. Absence of history says nothing about
  the person.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from features import keystroke as ks
from features import mouse as ms
from features import session as se
from features import shape as sh
from features import transactional as tx
from models.base import ModelContext
from scoring.combine import (CalibrationSet, attenuate, blend, confidence,
                             coverage_status)

FULL_CONFIDENCE_EVENTS = 40
FULL_CONFIDENCE_SESSIONS = 20
MIN_SCOREABLE_EVENTS = 3
MIN_SCOREABLE_SESSIONS = 3

# Preview flag thresholds.
HOUR_UNUSUAL_AT = 3.0       # concentration-weighted hours from the user's norm
VELOCITY_HIGH_AT = 3        # transactions in the preceding hour
DORMANT_DAYS = 30


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def _penalty(calset: CalibrationSet, dim: str, fallback: float) -> float:
    cal = calset.calibrators.get(dim)
    return cal.impostor_mean if cal else fallback


# ------------------------------------------------------- challenge dims


def score_keystroke(tpl: dict | None, events: list[dict],
                    calset: CalibrationSet) -> tuple[dict, float | None]:
    if tpl is None:
        return {"status": "INSUFFICIENT_DATA", "reason": "no keystroke template"}, None

    sample = ks.extract(events)
    cov = sample["coverage"]
    observed = ks.distance(tpl, sample)
    d = blend(observed, _penalty(calset, "keystroke", 3.0), cov)
    p = calset.probability("keystroke", d)
    return {
        "status": coverage_status(cov),
        "coverage": _r(cov, 3),
        "observedDistance": _r(observed),
        "distance": _r(d),
        "probability": _r(p),
        "summary": sample["summary"],
    }, p


def score_shape(tpl: dict | None, points: list[dict], calset: CalibrationSet,
                applicable: bool = True) -> tuple[dict, float | None]:
    if not applicable:
        # The device cannot trace at all -- a capability limit, not a
        # skipped answer. Excluded rather than penalised.
        return {"status": "NOT_APPLICABLE", "reason": "no pointer or touch input"}, None
    if tpl is None or "split_mean" not in tpl:
        return {"status": "INSUFFICIENT_DATA",
                "reason": "no shape template for the current shape -- re-enrol"}, None

    sample = sh.extract(points)
    cov = sample["coverage"]
    observed = sh.distance(tpl, sample)
    d = blend(observed, _penalty(calset, "shape", 3.0), cov)
    p = calset.probability("shape", d)
    return {
        "status": coverage_status(cov),
        "coverage": _r(cov, 3),
        "observedDistance": _r(observed),
        "distance": _r(d),
        "probability": _r(p),
        "summary": sample["summary"],
    }, p


def score_mouse(tpl: dict | None, points: list[dict], clicks: list[dict],
                calset: CalibrationSet) -> tuple[dict, float | None]:
    """Reported for inspection; excluded from every score by default."""
    if tpl is None:
        return {"status": "INSUFFICIENT_DATA", "reason": "no mouse template"}, None
    if len(points) < ms.MIN_POINTS:
        return {"status": "INSUFFICIENT_DATA",
                "pointsCaptured": len(points), "pointsRequired": ms.MIN_POINTS}, None
    sample = ms.extract(points, clicks)
    d = ms.distance(tpl, sample)
    p = calset.probability("mouse", d)
    return {"status": "OK" if d is not None else "INSUFFICIENT_DATA",
            "distance": _r(d), "probability": _r(p)}, p


# --------------------------------------------------------- passive dims


def score_session(features: dict | None, history: list[dict],
                  calset: CalibrationSet) -> tuple[dict, float | None]:
    if features is None:
        return {"status": "NOT_PROVIDED"}, None
    profile = se.build_profile(history)
    if profile is None or len(history) < MIN_SCOREABLE_SESSIONS:
        return {"status": "INSUFFICIENT_DATA", "sessionsSeen": len(history),
                "sessionsRequired": MIN_SCOREABLE_SESSIONS}, None
    d = se.distance(profile, features)
    raw = calset.probability("session", d)
    conf = confidence(len(history), FULL_CONFIDENCE_SESSIONS)
    p = attenuate(raw, conf) if raw is not None else None
    return {"status": "OK" if conf >= 1.0 else "WARMING_UP",
            "distance": _r(d), "probability": _r(p), "rawProbability": _r(raw),
            "confidence": _r(conf, 2), "sessionsSeen": len(history)}, p


def score_transaction(event: dict, history: list[dict], state: dict[str, Any],
                      artifacts) -> tuple[dict, float | None]:
    if len(history) < MIN_SCOREABLE_EVENTS:
        return {"status": "INSUFFICIENT_DATA", "eventsSeen": len(history),
                "eventsRequired": MIN_SCOREABLE_EVENTS}, None
    if not state.get("ready"):
        return {"status": "INSUFFICIENT_DATA",
                "reason": "transactional model not loaded"}, None

    fv = tx.compute_one(event, [_hist_row(e) for e in history])
    if fv is None:
        return {"status": "INSUFFICIENT_DATA"}, None

    frame = pd.DataFrame([fv.as_dict()])
    model = state["model"]
    ctx = ModelContext(data=frame, artifact_dir=artifacts)
    model.validate_features(ctx)
    score = model.infer(ctx)
    raw = float(state["tx_calibrator"].transform(score)[0])
    conf = confidence(len(history), FULL_CONFIDENCE_EVENTS)
    p = attenuate(raw, conf)
    return {"status": "OK" if conf >= 1.0 else "WARMING_UP",
            "distance": _r(float(score[0])), "probability": _r(p),
            "rawProbability": _r(raw), "confidence": _r(conf, 2),
            "eventsSeen": len(history),
            "features": {k: _r(v, 3) for k, v in fv.as_dict().items()}}, p


def _hist_row(e: dict) -> dict:
    return {"user_id": e["user_id"], "amount": e["amount"] or 0.0,
            "beneficiary_id": e["beneficiary_id"] or "",
            "device_id": e["device_id"], "city": e["city"],
            "occurred_at": e["occurred_at"]}


# ---------------------------------------------------------------- flags


def preview_flags(event: dict, history: list[dict]) -> dict:
    """
    Booleans for the decision layer, computed from history set membership.

    Available from the very first event -- "this device has not been seen
    before" is a fact even with an empty history. Flags that need a
    baseline (unusual hour, dormancy) are None until one exists, rather
    than defaulting to a guess.
    """
    devices = {e["device_id"] for e in history}
    cities = {e["city"] for e in history}
    payees = {e["beneficiary_id"] for e in history if e["beneficiary_id"]}
    t = event["occurred_at"]

    recent = sum(1 for e in history if 0 <= t - e["occurred_at"] <= 3600)
    last = max((e["occurred_at"] for e in history), default=None)

    hour_unusual = None
    if len(history) >= MIN_SCOREABLE_EVENTS:
        hours = np.array([datetime.fromtimestamp(e["occurred_at"], timezone.utc).hour
                          for e in history])
        dev = tx._circular_hour_deviation(datetime.fromtimestamp(t, timezone.utc).hour, hours)
        hour_unusual = bool(dev >= HOUR_UNUSUAL_AT)

    return {
        "deviceSeenBefore": event["device_id"] in devices,
        "locationSeenBefore": event["city"] in cities,
        "payeeSeenBefore": bool(event["beneficiary_id"]) and event["beneficiary_id"] in payees,
        "hourUnusual": hour_unusual,
        "velocityHigh": recent >= VELOCITY_HIGH_AT,
        "dormant": None if last is None else bool((t - last) / 86400 > DORMANT_DAYS),
    }
