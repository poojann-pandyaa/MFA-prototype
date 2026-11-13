"""
Serving side of the payment-risk model: load the trained artifacts, score one payment, explain it.

    scorer = get_scorer()                       # None if no artifacts were found
    r = scorer.score(event, user_state, attested=True)
    r.severity, r.threat, r.action, r.reasons   # reasons are for logs/analysts, never for the client

`user_state` must be built by replaying the user's past payments through UserState.update (the
same code the simulator/training used), see main.txn_risk_assess.

Three heads score the payment (takeover, coached, bad payee). Each score becomes a "surprise" against the
legitimate-payment score distribution; severity comes from the largest surprise (so the friction budget holds
across all three) and a small router, trained on fraud payments only, names the family (takeover, coached, payee).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np

from ato.features import FEATURES, Event, UserState, featurize

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "ato"
HEADS = ["takeover", "coached", "payee"]
THREAT = {"takeover": "UNAUTHORISED", "coached": "COACHED", "payee": "PAYEE_RISK"}
ACTIONS = {
    "UNAUTHORISED": {"MEDIUM": "device_biometric_reprompt", "HIGH": "face_challenge", "CRITICAL": "block_and_lock_binding"},
    "COACHED": {"MEDIUM": "scam_warning", "HIGH": "cooling_off_hold", "CRITICAL": "block_and_offer_report"},
    "PAYEE_RISK": {"MEDIUM": "payee_risk_badge", "HIGH": "hold_and_verify_payee", "CRITICAL": "block"},
}
HOLD_MINUTES = 30
HOLD_ACTIONS = {"cooling_off_hold", "hold_and_verify_payee"}
BLOCK_ACTIONS = {"block_and_lock_binding", "block_and_offer_report", "block"}
LEVELS = ("CRITICAL", "HIGH", "MEDIUM")  # checked in this order


@dataclass
class Result:
    severity: str                       # LOW | MEDIUM | HIGH | CRITICAL
    action: str
    score: float                        # largest surprise over the heads
    variant: str                        # full | server_only | fallback_rules
    reasons: List[Tuple[str, float]] = field(default_factory=list)   # (feature, surprise drop when neutralised); server-side only
    threat: str = "NONE"                # UNAUTHORISED | COACHED | PAYEE_RISK | NONE
    surprises: Dict[str, float] = field(default_factory=dict)


def _surprise(grid: dict, s: np.ndarray) -> np.ndarray:
    t = np.interp(s, np.asarray(grid["scores"]), np.asarray(grid["tails"]))
    return -np.log(np.clip(t, 1e-5, 1.0))


def _severity(u: float, thr: dict) -> str:
    return next((lvl for lvl in LEVELS if u > thr[lvl]), "LOW")


class AtoScorer:
    def __init__(self, model_dir: Path = MODEL_DIR):
        self.card = json.loads((model_dir / "model_card.json").read_text())
        if self.card["features"] != FEATURES:
            raise RuntimeError("model_card features differ from ato.features.FEATURES; retrain the model")
        self.neutral = np.array([np.nan if v is None else v for v in self.card["neutral"]], dtype=np.float64)
        self.variants = {n: joblib.load(model_dir / f"ato_{n}.joblib") for n in ("full", "server_only")}

    def _head_surprise(self, name: str, head: str, rows: np.ndarray) -> np.ndarray:
        b = self.variants[name]["heads"][head]
        p = b["model"].predict_proba(rows[:, b["idx"]])[:, 1]
        return _surprise(self.card["variants"][name]["grids"][head], p)

    def score(self, ev: Event, state: UserState, attested: bool) -> Result:
        name = "full" if attested else "server_only"
        thr = self.card["variants"][name]["thresholds"]
        x = np.asarray(featurize(ev, state), dtype=np.float64)[None, :]
        us = {h: float(self._head_surprise(name, h, x)[0]) for h in HEADS}
        # severity from the most surprised head; a small router (trained on fraud payments) names the family, so the action fits the scam
        top = max(HEADS, key=lambda h: us[h])
        sev = _severity(us[top], thr)
        if sev == "LOW":
            return Result("LOW", "allow", us[top], name, [], "NONE", us)
        # explain the winning head: neutralise one feature at a time, one batched call
        r = self.variants[name]["router"]
        top = HEADS[int(r["model"].predict(x[:, r["idx"]])[0])]
        idx = self.variants[name]["heads"][top]["idx"]
        batch = np.tile(x, (len(idx) + 1, 1))
        for r, j in enumerate(idx, start=1):
            batch[r, j] = self.neutral[j]
        u = self._head_surprise(name, top, batch)
        drops = sorted(((FEATURES[j], float(u[0] - u[r])) for r, j in enumerate(idx, start=1)), key=lambda t: -t[1])
        reasons = [(f, round(d, 2)) for f, d in drops[:3] if d > 0.5]
        threat = THREAT[top]
        return Result(sev, ACTIONS[threat][sev], us[top], name, reasons, threat, us)


def fallback(ev: Event) -> Result:
    """Used only if the trained artifacts are missing: a handful of hard rules, deliberately blunt."""
    fresh = lambda v, h: v is not None and v < h  # noqa: E731
    new_bind = fresh(ev.device_bound_hours, 24)
    if ev.remote_access_active or (new_bind and (fresh(ev.sim_changed_hours, 72) or fresh(ev.pin_reset_hours, 24))):
        sev, threat = "HIGH", "UNAUTHORISED"
    elif new_bind or fresh(ev.sim_changed_hours, 72):
        sev, threat = "MEDIUM", "UNAUTHORISED"
    elif ev.payee_fri is not None and ev.payee_fri >= 3:
        sev, threat = "HIGH", "PAYEE_RISK"
    else:
        sev, threat = "LOW", "NONE"
    return Result(sev, ACTIONS[threat][sev] if sev != "LOW" else "allow", 0.0, "fallback_rules", [], threat, {})


_scorer: Optional[AtoScorer] = None
_tried = False


def get_scorer() -> Optional[AtoScorer]:
    global _scorer, _tried
    if not _tried:
        _tried = True
        try:
            _scorer = AtoScorer()
        except Exception:  # missing/stale artifacts: caller falls back to rules
            _scorer = None
    return _scorer


def psi(card: dict, feature: str, values) -> float:
    """Population Stability Index of live `values` against the training decile bins (0.1 ~ watch, 0.25 ~ retrain)."""
    edges = np.asarray(card["feature_deciles"][feature])
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return float("nan")
    live = np.bincount(np.searchsorted(edges, v), minlength=10) / len(v)
    exp = np.full(10, 0.1)
    live = np.clip(live, 1e-4, None)
    return float(((live - exp) * np.log(live / exp)).sum())
