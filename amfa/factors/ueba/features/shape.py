from __future__ import annotations

import numpy as np

SHAPE = [
    (0.08, 0.75),
    (0.30, 0.20),
    (0.50, 0.78),
    (0.70, 0.20),
    (0.92, 0.72),
]

N_CHECKPOINTS = 24

MIN_POINTS = 40
MIN_SAMPLES = 5
MAX_DEVIATION = 10.0
STD_FLOOR_DEV = 0.008
STD_FLOOR_SPEED = 0.05
STD_FLOOR_TIME = 15.0


def _segments() -> tuple[np.ndarray, np.ndarray, float]:
    pts = np.array(SHAPE, dtype=float)
    starts = pts[:-1]
    vecs = pts[1:] - pts[:-1]
    lengths = np.hypot(vecs[:, 0], vecs[:, 1])
    return starts, vecs, float(lengths.sum())


def _project(xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
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
                best_dev = float(np.sign(v[0] * diff[1] - v[1] * diff[0])
                                 * np.sqrt(d2))
        progress[i] = best_prog
        deviation[i] = best_dev

    return progress, deviation


def extract(points: list[dict]) -> dict | None:
    if not points or len(points) < MIN_POINTS:
        return None

    xy = np.array([[p["x"], p["y"]] for p in points], dtype=float)
    t = np.array([p["t"] for p in points], dtype=float)
    t = t - t[0]

    progress, deviation = _project(xy)

    if progress.max() < 0.85:
        return None

    running = np.maximum.accumulate(progress)

    checkpoints = np.linspace(0.0, 1.0, N_CHECKPOINTS)

    dev_at = np.interp(checkpoints, running, deviation)
    time_at = np.interp(checkpoints, running, t)

    step = 1.0 / (N_CHECKPOINTS - 1)
    dt = np.diff(time_at) / 1000.0
    speed_at = np.divide(step, dt, out=np.zeros_like(dt),
                         where=dt > 1e-6)

    total_ms = float(t[-1])
    return {
        "deviation": dev_at,
        "speed": speed_at,
        "time": time_at,
        "summary": {
            "total_ms": total_ms,
            "mean_abs_deviation": float(np.abs(deviation).mean()),
            "max_deviation": float(np.abs(deviation).max()),
            "n_points": len(points),
            "completion": float(progress.max()),
        },
    }


def build_template(samples: list[dict]) -> dict | None:
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
    if template is None or sample is None:
        return None

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
