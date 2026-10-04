"""
Shape-trace dynamics.

The user traces the same fixed path every time, so position k along the
path can be compared against position k along the path -- the same
constraint that makes keystroke dynamics work. Free-form pointer movement
failed in live testing precisely because it had no such constraint.

The path is an open trapezoid: a 60 degree rise, a flat top, a 60 degree
fall. Three segments of roughly equal length, so every part of the trace
carries similar weight. Open, non-crossing, and with no two segments
running close and parallel, which is what the projection below needs.

Every attempt is scored. A trace that stops halfway has coverage of about
0.5; the caller blends the observed distance toward a typical impostor's
in proportion to the part that is missing (see scoring.combine.blend).
"""

from __future__ import annotations

import warnings

import numpy as np

# The trace pad is locked to this width:height ratio in the frontend. The
# shape is defined in normalised pad coordinates, and the 60 degree slopes
# are only 60 degrees on screen at this ratio -- dx = dy / (tan60 * ASPECT).
ASPECT = 2.0

SHAPE = [
    (0.150, 0.80),
    (0.323, 0.20),
    (0.677, 0.20),
    (0.850, 0.80),
]

N_CHECKPOINTS = 24

MIN_POINTS = 10           # fewer than this and the trace says nothing
MIN_SAMPLES = 4           # enrollment traces
MIN_ENROLL_COVERAGE = 0.9 # an enrollment trace must reach (nearly) the end
MAX_DEVIATION = 10.0
STD_FLOOR_DEV = 0.008     # pad heights
STD_FLOOR_SPLIT = 8.0     # milliseconds


def _iso(xy: np.ndarray) -> np.ndarray:
    """Scale x by the aspect ratio so distances are equal in both axes."""
    out = np.array(xy, dtype=float)
    out[..., 0] *= ASPECT
    return out


def _segments():
    pts = _iso(np.array(SHAPE))
    starts, vecs = pts[:-1], pts[1:] - pts[:-1]
    lengths = np.hypot(vecs[:, 0], vecs[:, 1])
    return starts, vecs, lengths


def _project(xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Snap each point onto the ideal path.

    Returns (progress 0-1 along the path, signed perpendicular deviation).
    The sign matters: cutting inside a corner and overshooting it are
    different behaviours and must not cancel.
    """
    starts, vecs, lengths = _segments()
    total = lengths.sum()
    cum = np.concatenate([[0.0], np.cumsum(lengths)])
    progress = np.empty(len(xy))
    deviation = np.empty(len(xy))
    for i, p in enumerate(xy):
        best = (np.inf, 0.0, 0.0)
        for s in range(len(starts)):
            v = vecs[s]
            t = float(np.clip((p - starts[s]) @ v / (v @ v), 0.0, 1.0))
            diff = p - (starts[s] + t * v)
            d2 = float(diff @ diff)
            if d2 < best[0]:
                sign = np.sign(v[0] * diff[1] - v[1] * diff[0])
                best = (d2, (cum[s] + t * lengths[s]) / total, float(sign * np.sqrt(d2)))
        _, progress[i], deviation[i] = best
    return progress, deviation


def _empty(n_points: int) -> dict:
    return {
        "deviation": np.full(N_CHECKPOINTS, np.nan),
        "split": np.full(N_CHECKPOINTS - 1, np.nan),
        "coverage": 0.0,
        "summary": {"n_points": n_points, "coverage": 0.0},
    }


def extract(points: list[dict] | None) -> dict:
    """
    Turn a traced path into per-checkpoint features.

    Each point: {"x": 0.31, "y": 0.44, "t": 1234.5}, x and y normalised to
    the pad, t in milliseconds. Always returns a result, never None.

    Per checkpoint:
      deviation  signed distance from the ideal line, in pad heights
      split      time taken to reach this checkpoint from the previous one

    Splits are used rather than cumulative elapsed time. Cumulative time
    carries every early hesitation forward into all later checkpoints, and
    for a trace that starts part way along the path there is no shared zero
    to measure from. Splits compare only what happened locally.
    """
    if not points or len(points) < MIN_POINTS:
        return _empty(len(points or []))

    xy = _iso(np.array([[p["x"], p["y"]] for p in points], dtype=float))
    t = np.array([p["t"] for p in points], dtype=float)
    t = t - t[0]

    progress, deviation = _project(xy)
    running = np.maximum.accumulate(progress)
    lo, hi = float(progress.min()), float(running.max())
    # Starting a hair after the green dot or stopping a hair before the red
    # one is not a missing section; snap ends within half a checkpoint.
    half = 0.5 / (N_CHECKPOINTS - 1)
    lo = 0.0 if lo <= half else lo
    hi = 1.0 if hi >= 1.0 - half else hi
    coverage = max(0.0, min(1.0, hi - lo))

    keep = np.concatenate([[0], np.where(np.diff(running) > 0)[0] + 1])
    xp = running[keep]
    if len(xp) < 2:
        return _empty(len(points))

    cps = np.linspace(0.0, 1.0, N_CHECKPOINTS)
    reached = (cps >= lo - 1e-9) & (cps <= hi + 1e-9)

    dev_at = np.full(N_CHECKPOINTS, np.nan)
    time_at = np.full(N_CHECKPOINTS, np.nan)
    dev_at[reached] = np.interp(cps[reached], xp, deviation[keep])
    time_at[reached] = np.interp(cps[reached], xp, t[keep])
    split = np.diff(time_at)            # NaN wherever either end was not reached

    return {
        "deviation": dev_at,
        "split": split,
        "coverage": coverage,
        "summary": {
            "n_points": len(points),
            "coverage": round(coverage, 3),
            "total_ms": float(t[-1]),
            "mean_abs_deviation": float(np.abs(deviation).mean()),
        },
    }


def _stats(mat: np.ndarray, floor: float) -> tuple[list, list]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(mat, axis=0)
        std = np.nanstd(mat, axis=0, ddof=1)
    std = np.where(np.isnan(std), floor, np.maximum(std, floor))
    to_list = lambda a: [None if np.isnan(v) else float(v) for v in a]
    return to_list(mean), to_list(std)


def build_template(samples: list[dict]) -> dict | None:
    """Mean and standard deviation at every checkpoint, from complete traces."""
    usable = [s for s in samples if s and s["coverage"] >= MIN_ENROLL_COVERAGE]
    if len(usable) < MIN_SAMPLES:
        return None
    dev_mean, dev_std = _stats(np.vstack([s["deviation"] for s in usable]), STD_FLOOR_DEV)
    split_mean, split_std = _stats(np.vstack([s["split"] for s in usable]), STD_FLOOR_SPLIT)
    return {
        "n_samples": len(usable),
        "n_checkpoints": N_CHECKPOINTS,
        "shape": SHAPE,
        "deviation_mean": dev_mean, "deviation_std": dev_std,
        "split_mean": split_mean, "split_std": split_std,
    }


def _arr(values) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def distance(template: dict | None, sample: dict | None) -> float | None:
    """Scaled Manhattan distance over the checkpoints the trace reached."""
    if template is None or sample is None or "split_mean" not in template:
        return None
    parts = []
    for key in ("deviation", "split"):
        obs = np.asarray(sample[key], dtype=float)
        mean, std = _arr(template[f"{key}_mean"]), _arr(template[f"{key}_std"])
        n = min(len(obs), len(mean))
        o, m, s = obs[:n], mean[:n], std[:n]
        ok = ~np.isnan(o) & ~np.isnan(m)
        if ok.any():
            parts.append(np.abs(o[ok] - m[ok]) / s[ok])
    if not parts:
        return None
    return float(np.clip(np.concatenate(parts), 0, MAX_DEVIATION).mean())
