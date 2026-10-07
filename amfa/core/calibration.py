from __future__ import annotations

import math
from typing import Optional

import numpy as np
from sqlalchemy import select

from amfa.core import db

MIN_GENUINE = 300
TARGET_FRR = 0.01
THRESHOLDS = {"FACE": 0.80, "VOICE": -0.6931471805599453}


def _wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c + r) / d


def report(tenant_id: Optional[str] = None, target_frr: float = TARGET_FRR) -> dict:
    with db.session() as s:
        q = select(db.Attempt, db.AuthSession.status, db.AuthSession.resolution).join(
            db.AuthSession, db.AuthSession.id == db.Attempt.session_id)
        if tenant_id:
            q = q.where(db.AuthSession.tenant_id == tenant_id)
        rows = s.execute(q).all()
    out: dict = {"targetFrr": target_frr, "minimumGenuineSamples": MIN_GENUINE, "modalities": {}}
    for factor in ("FACE", "VOICE"):
        genuine = np.array([a.liveness_score for a, st, _ in rows
                            if a.factor == factor and st == "VERIFIED" and a.liveness_score is not None])
        attacks = np.array([a.liveness_score for a, st, res in rows
                            if a.factor == factor and res == "PRESENTATION_ATTACK" and a.liveness_score is not None])
        thr = THRESHOLDS[factor]
        m: dict = {"threshold": thr, "genuine": int(len(genuine)), "attacks": int(len(attacks))}
        if len(genuine):
            flagged = int((genuine <= thr).sum()) if factor == "VOICE" else int((genuine < thr).sum())
            m["frrAtThreshold"] = round(flagged / len(genuine), 4)
            m["frrUpper95"] = round(_wilson_upper(flagged, len(genuine)), 4)
            m["thresholdFor"] = {f"{int(t * 100)}%FRR": round(float(np.quantile(genuine, t)), 4)
                                 for t in (0.01, 0.02, 0.05)}
            if len(attacks):
                t1 = m["thresholdFor"]["1%FRR"]
                m["attackAcceptanceAt1%FRR"] = round(float((attacks >= t1).mean()), 4)
        ok = len(genuine) >= MIN_GENUINE and m.get("frrUpper95", 1.0) <= target_frr
        m["terminalSupported"] = ok
        m["recommendation"] = (
            "terminal is supportable at the current threshold" if ok else
            f"keep advisory: need >= {MIN_GENUINE} genuine samples and an FRR upper bound <= {target_frr:.0%}")
        match = np.array([a.score for a, st, _ in rows if a.factor == factor and st == "VERIFIED" and a.score is not None])
        if len(match):
            m["matchScores"] = {"n": int(len(match)), "p01": round(float(np.quantile(match, 0.01)), 4),
                                "median": round(float(np.median(match)), 4),
                                "note": "biased: only passed attempts appear in verified sessions"}
        out["modalities"][factor] = m
    return out
