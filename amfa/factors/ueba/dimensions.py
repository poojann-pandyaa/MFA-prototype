from __future__ import annotations

from typing import Optional

from amfa.factors.ueba.features import keystroke as ks
from amfa.factors.ueba.features import mouse as ms
from amfa.factors.ueba.features import shape as sh
from amfa.factors.ueba.scoring.combine import CalibrationSet, blend, coverage_status

# Challenge dimensions (keystroke, shape): the user was asked to do something, so an empty or
# partial answer is scored, blended toward a typical impostor by how much is missing, and never
# skipped. Passive dimensions (session, transaction) have no such request: no history means
# nothing to compare, so they report INSUFFICIENT_DATA and are left out of the score.

IMPOSTOR_FALLBACK = 3.0


def _r(v, n: int = 4) -> Optional[float]:
    return None if v is None else round(float(v), n)


def _penalty(calset: CalibrationSet, dim: str) -> float:
    cal = calset.calibrators.get(dim)
    return cal.impostor_mean if cal else IMPOSTOR_FALLBACK


def calibration_key(calset: CalibrationSet, dim: str, input: str) -> tuple[str, bool]:
    """Which calibrator scores this dimension for this input, and whether it is a stand-in.
    Keyboard uses the dimension's own ("keystroke", "shape"). Touch uses "keystroke_touch" /
    "shape_touch" once real measurements are fitted; until then it borrows the keyboard
    calibrator and is reported as provisional."""
    if input == "keyboard":
        return dim, False
    key = f"{dim}_{input}"
    if key in calset.calibrators:
        return key, False
    return dim, True


def _challenge(dim: str, module, tpl: dict, sample: dict, calset: CalibrationSet, input: str):
    key, provisional = calibration_key(calset, dim, input)
    cov = sample["coverage"]
    observed = module.distance(tpl, sample)
    d = blend(observed, _penalty(calset, key), cov)
    p = calset.probability(key, d)
    return {"status": coverage_status(cov), "input": input, "calibration": key,
            "provisional": provisional, "coverage": _r(cov, 3), "observedDistance": _r(observed),
            "distance": _r(d), "probability": _r(p), "summary": sample["summary"]}, p


def score_keystroke(tpl: Optional[dict], events: list[dict], calset: CalibrationSet,
                    input: str = "keyboard") -> tuple[dict, Optional[float]]:
    if tpl is None:
        return {"status": "INSUFFICIENT_DATA", "input": input,
                "reason": f"no keystroke template for {input}"}, None
    return _challenge("keystroke", ks, tpl, ks.extract(events), calset, input)


def score_shape(tpl: Optional[dict], points: list[dict], calset: CalibrationSet,
                applicable: bool = True, input: str = "keyboard") -> tuple[dict, Optional[float]]:
    if not applicable:
        return {"status": "NOT_APPLICABLE", "reason": "no pointer or touch input"}, None
    if tpl is None or "split_mean" not in tpl:
        return {"status": "INSUFFICIENT_DATA", "input": input,
                "reason": f"no shape template for {input}; enrol this input"}, None
    return _challenge("shape", sh, tpl, sh.extract(points), calset, input)


def score_mouse(tpl: Optional[dict], points: list[dict], clicks: list[dict],
                calset: CalibrationSet) -> tuple[dict, Optional[float]]:
    """Reported for inspection; excluded from every score."""
    if tpl is None:
        return {"status": "INSUFFICIENT_DATA", "reason": "no mouse template"}, None
    if len(points) < ms.MIN_POINTS:
        return {"status": "INSUFFICIENT_DATA", "pointsCaptured": len(points),
                "pointsRequired": ms.MIN_POINTS}, None
    sample = ms.extract(points, clicks)
    d = ms.distance(tpl, sample)
    p = calset.probability("mouse", d)
    return {"status": "OK" if d is not None else "INSUFFICIENT_DATA",
            "distance": _r(d), "probability": _r(p)}, p
