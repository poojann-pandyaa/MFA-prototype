"""
Shape-trace mouse dynamics.

Free-form pointer movement did not work. Calibrated on simulated traces it
measured EER 0.098; in live two-person testing it flagged most genuine
attempts as impostor. The reason is structural: every free-form trace is a
different path, so only aggregate statistics are comparable, and
within-person variation between sittings exceeded between-person
variation.

This module applies the same constraint that makes keystroke dynamics
work. Keystroke compares position 3 at enrollment against position 3 at
verification because the phrase is fixed. Here the user traces the same
shape every time, so progress 40% along the path can be compared against
progress 40% along the path.

What remains after the task is held constant is the person: how precisely
they track a line, where they accelerate, whether they cut corners or
overshoot them.

There is theory behind expecting this to work. Fitts's law gives movement
time as a function of distance and target width; with those held fixed,
the remaining variation is the individual's motor constants. Free-form
movement confounds those constants with the task.
"""

from __future__ import annotations

import numpy as np

# The traced shape, in normalised coordinates (0-1 on both axes).
#
# A zigzag rather than a smooth curve: corners are where individual style
# shows most clearly. Some people round them, some overshoot and correct,
# some cut inside. A circle would lose that.
SHAPE = [
    (0.08, 0.75),
    (0.30, 0.20),
    (0.50, 0.78),
    (0.70, 0.20),
    (0.92, 0.72),
]

# Checkpoints along the path. Each becomes a comparable position, the way
# each key position is comparable in the keystroke template.
N_CHECKPOINTS = 24

MIN_POINTS = 40          # a trace shorter than this says nothing
MIN_SAMPLES = 5
MAX_DEVIATION = 10.0
STD_FLOOR_DEV = 0.008    # normalised units
STD_FLOOR_SPEED = 0.05
STD_FLOOR_TIME = 15.0    # milliseconds


def _segments() -> tuple[np.ndarray, np.ndarray, float]:
    """Ideal path as segment starts, direction vectors, and total length."""
    pts = np.array(SHAPE, dtype=float)
    starts = pts[:-1]
    vecs = pts[1:] - pts[:-1]
    lengths = np.hypot(vecs[:, 0], vecs[:, 1])
    return starts, vecs, float(lengths.sum())


def _project(xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Project each observed point onto the ideal path.

    Returns (progress, signed_deviation) where progress is 0-1 along the
    path and deviation is signed perpendicular distance -- positive on one
    side, negative on the other. The sign matters: cutting inside a corner
    and overshooting it are different behaviours and should not cancel.
    """
    starts, vecs, total = _segments()
    lengths = np.hypot(vecs[:, 0], vecs[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(lengths)])

    progress = np.empty(len(xy))
    deviation = np.empty(len(xy))

    for i, p in enumerate(xy):
        best_d2, best_prog, best_dev = np.inf, 0.0, 0.0
        for s in range(len(starts)):
            v = vecs[s]
            vv = float(v @ v)
            if vv == 0:
                continue
            t = float(np.clip((p - starts[s]) @ v / vv, 0.0, 1.0))
            foot = starts[s] + t * v
            diff = p - foot
            d2 = float(diff @ diff)
            if d2 < best_d2:
                best_d2 = d2
                best_prog = (cum[s] + t * lengths[s]) / total
                # Sign from the 2D cross product: which side of the
                # segment the point falls on.
                best_dev = float(np.sign(v[0] * diff[1] - v[1] * diff[0])
                                 * np.sqrt(d2))
        progress[i] = best_prog
        deviation[i] = best_dev

    return progress, deviation


def extract(points: list[dict]) -> dict | None:
    """
    Turn a traced path into per-checkpoint features.

    Each point: {"x": 0.31, "y": 0.44, "t": 1234.5} with x,y normalised to
    the canvas and t in milliseconds.

    Returns per-checkpoint deviation, speed and elapsed time, plus a few
    whole-trace summaries.
    """
    if not points or len(points) < MIN_POINTS:
        return None

    xy = np.array([[p["x"], p["y"]] for p in points], dtype=float)
    t = np.array([p["t"] for p in points], dtype=float)
    t = t - t[0]

    progress, deviation = _project(xy)

    # Progress must advance. Backtracking is normal at corners, but a trace
    # that never gets near the end was abandoned.
    if progress.max() < 0.85:
        return None

    # Enforce monotonic progress for interpolation, keeping the furthest
    # point reached so far.
    running = np.maximum.accumulate(progress)

    checkpoints = np.linspace(0.0, 1.0, N_CHECKPOINTS)

    dev_at = np.interp(checkpoints, running, deviation)
    time_at = np.interp(checkpoints, running, t)

    # Speed between consecutive checkpoints, in path-units per second.
    step = 1.0 / (N_CHECKPOINTS - 1)
    dt = np.diff(time_at) / 1000.0
    speed_at = np.divide(step, dt, out=np.zeros_like(dt),
                         where=dt > 1e-6)

    total_ms = float(t[-1])
    return {
        "deviation": dev_at,                 # N_CHECKPOINTS
        "speed": speed_at,                   # N_CHECKPOINTS - 1
        "time": time_at,                     # N_CHECKPOINTS
        "summary": {
            "total_ms": total_ms,
            "mean_abs_deviation": float(np.abs(deviation).mean()),
            "max_deviation": float(np.abs(deviation).max()),
            "n_points": len(points),
            "completion": float(progress.max()),
        },
    }


def build_template(samples: list[dict]) -> dict | None:
    """
    Mean and standard deviation at every checkpoint.

    Stores variance, not just a mean path. Without it a 0.02 deviation
    cannot be told apart between a slightly rushed genuine user and an
    impostor -- the same reasoning as the keystroke template.
    """
    usable = [s for s in samples if s is not None]
    if len(usable) < MIN_SAMPLES:
        return None

    def stats(key: str, floor: float):
        mat = np.vstack([s[key] for s in usable])
        mean = mat.mean(axis=0)
        std = np.maximum(mat.std(axis=0, ddof=1), floor)
        return mean.tolist(), std.tolist()

    dev_mean, dev_std = stats("deviation", STD_FLOOR_DEV)
    speed_mean, speed_std = stats("speed", STD_FLOOR_SPEED)
    time_mean, time_std = stats("time", STD_FLOOR_TIME)

    totals = np.array([s["summary"]["total_ms"] for s in usable])

    return {
        "n_samples": len(usable),
        "n_checkpoints": N_CHECKPOINTS,
        "deviation_mean": dev_mean, "deviation_std": dev_std,
        "speed_mean": speed_mean, "speed_std": speed_std,
        "time_mean": time_mean, "time_std": time_std,
        "total_ms_mean": float(totals.mean()),
        "total_ms_std": float(max(totals.std(ddof=1), 50.0)),
    }


def distance(template: dict, sample: dict) -> float | None:
    """
    Scaled Manhattan distance across checkpoints.

    Deviation, speed and timing are each compared position by position and
    scaled by that position's own variability, then averaged. Per-position
    contributions are clipped so one corner cannot dominate.
    """
    if template is None or sample is None:
        return None

    # Speed is excluded: it is computed from consecutive time values, so
    # comparing both double-counts. Measured across three seeds,
    # deviation+time gives EER 0.153 against 0.154 for all three.
    parts = []
    for key in ("deviation", "time"):
        obs = np.asarray(sample[key], dtype=float)
        mean = np.asarray(template[f"{key}_mean"])
        std = np.asarray(template[f"{key}_std"])
        n = min(len(obs), len(mean))
        if n == 0:
            continue
        parts.append(np.abs(obs[:n] - mean[:n]) / std[:n])

    if not parts:
        return None

    scaled = np.clip(np.concatenate(parts), 0, MAX_DEVIATION)
    return float(scaled.mean())
