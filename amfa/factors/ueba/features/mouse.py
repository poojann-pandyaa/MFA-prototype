from __future__ import annotations

import numpy as np

MIN_POINTS = 30
MIN_SAMPLES = 3
MAX_DEVIATION = 10.0


def _segments(points: list[dict]) -> tuple[np.ndarray, ...]:
    xy = np.array([[p["x"], p["y"]] for p in points], dtype=float)
    t = np.array([p["t"] for p in points], dtype=float)

    d_xy = np.diff(xy, axis=0)
    dt = np.diff(t)

    ok = dt > 0
    return d_xy[ok], dt[ok], xy


def extract(points: list[dict], clicks: list[dict] | None = None) -> dict | None:
    if not points or len(points) < MIN_POINTS:
        return None

    d_xy, dt, xy = _segments(points)
    if len(dt) < 10:
        return None

    dist = np.hypot(d_xy[:, 0], d_xy[:, 1])
    velocity = dist / dt

    accel = np.diff(velocity) / dt[1:] if len(velocity) > 1 else np.array([0.0])

    path_len = float(dist.sum())
    straight = float(np.hypot(*(xy[-1] - xy[0])))
    efficiency = straight / path_len if path_len > 0 else 0.0

    headings = np.arctan2(d_xy[:, 1], d_xy[:, 0])
    turns = np.diff(headings)
    turns = np.arctan2(np.sin(turns), np.cos(turns))
    curvature = float(np.abs(turns).mean()) if len(turns) else 0.0
    direction_changes = float((np.abs(turns) > np.pi / 4).mean()) if len(turns) else 0.0

    jitter = float(velocity.std() / velocity.mean()) if velocity.mean() > 0 else 0.0

    click_dwell = 0.0
    click_interval = 0.0
    if clicks:
        dwells = [c["up"] - c["down"] for c in clicks if c.get("up")]
        click_dwell = float(np.mean(dwells)) if dwells else 0.0
        if len(clicks) > 1:
            gaps = [
                clicks[i + 1]["down"] - clicks[i]["down"]
                for i in range(len(clicks) - 1)
            ]
            click_interval = float(np.mean(gaps))

    return {
        "velocity_mean": float(velocity.mean()),
        "velocity_std": float(velocity.std()),
        "velocity_max": float(velocity.max()),
        "accel_mean": float(np.abs(accel).mean()),
        "accel_std": float(accel.std()),
        "path_efficiency": float(efficiency),
        "curvature": curvature,
        "direction_changes": direction_changes,
        "jitter": jitter,
        "click_dwell": click_dwell,
        "click_interval": click_interval,
    }


ALL_FEATURES = [
    "velocity_mean", "velocity_std", "velocity_max",
    "accel_mean", "accel_std",
    "path_efficiency", "curvature", "direction_changes", "jitter",
    "click_dwell", "click_interval",
]

COMPARED_FEATURES = [
    "accel_mean",
    "direction_changes",
    "click_dwell",
    "click_interval",
]

FEATURE_ORDER = COMPARED_FEATURES


def build_template(samples: list[dict]) -> dict | None:
    usable = [s for s in samples if s is not None]
    if len(usable) < MIN_SAMPLES:
        return None

    mat = np.array([[s[f] for f in FEATURE_ORDER] for s in usable], dtype=float)
    mean = mat.mean(axis=0)
    std = mat.std(axis=0, ddof=1) if len(usable) > 1 else np.ones(len(FEATURE_ORDER))

    floor = np.maximum(np.abs(mean) * 0.20, 1e-6)
    std = np.maximum(std, floor)

    return {
        "n_samples": len(usable),
        "features": FEATURE_ORDER,
        "mean": mean.tolist(),
        "std": std.tolist(),
    }


def distance(template: dict, sample: dict) -> float | None:
    if template is None or sample is None:
        return None

    obs = np.array([sample[f] for f in template["features"]], dtype=float)
    mean = np.asarray(template["mean"])
    std = np.asarray(template["std"])
    scaled = np.clip(np.abs(obs - mean) / std, 0, MAX_DEVIATION)
    return float(scaled.mean())
