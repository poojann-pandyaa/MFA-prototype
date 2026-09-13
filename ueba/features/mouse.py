"""
Mouse dynamics.

Collected passively while the user completes the enrollment or transaction
form. No artificial drawing task: mouse behaviour is task-dependent, so
movement during a tracing exercise says little about movement during form
entry. Collecting during the real flow means enrollment and verification
conditions match.

Feature families follow the mouse dynamics literature -- velocity,
acceleration, curvature, path efficiency, click timing and jitter.

Note: mouse dynamics needs more data than keystroke dynamics to be
reliable, and does not apply on touch devices at all. Treated here as a
supporting signal rather than a primary one.
"""

from __future__ import annotations

import numpy as np

MIN_POINTS = 30          # below this a movement trace says nothing
MIN_SAMPLES = 3
MAX_DEVIATION = 10.0     # cap on any single feature's contribution


def _segments(points: list[dict]) -> tuple[np.ndarray, ...]:
    """Per-move deltas: distance, elapsed time, and heading."""
    xy = np.array([[p["x"], p["y"]] for p in points], dtype=float)
    t = np.array([p["t"] for p in points], dtype=float)

    d_xy = np.diff(xy, axis=0)
    dt = np.diff(t)

    # Browsers emit repeated timestamps under coalescing; drop zero-time
    # segments rather than dividing by zero.
    ok = dt > 0
    return d_xy[ok], dt[ok], xy


def extract(points: list[dict], clicks: list[dict] | None = None) -> dict | None:
    """
    Turn a pointer trace into a feature vector.

    Each point: {"x": 412, "y": 300, "t": 1234.5} with t in milliseconds.
    Each click: {"down": 1234.5, "up": 1290.1}.
    """
    if not points or len(points) < MIN_POINTS:
        return None

    d_xy, dt, xy = _segments(points)
    if len(dt) < 10:
        return None

    dist = np.hypot(d_xy[:, 0], d_xy[:, 1])
    velocity = dist / dt                       # px per ms

    accel = np.diff(velocity) / dt[1:] if len(velocity) > 1 else np.array([0.0])

    # Path efficiency: straight-line distance over distance actually
    # travelled. A direct mover approaches 1; a wanderer approaches 0.
    path_len = float(dist.sum())
    straight = float(np.hypot(*(xy[-1] - xy[0])))
    efficiency = straight / path_len if path_len > 0 else 0.0

    # Heading changes. Curvature is the mean absolute turn per segment.
    headings = np.arctan2(d_xy[:, 1], d_xy[:, 0])
    turns = np.diff(headings)
    turns = np.arctan2(np.sin(turns), np.cos(turns))    # wrap to [-pi, pi]
    curvature = float(np.abs(turns).mean()) if len(turns) else 0.0
    direction_changes = float((np.abs(turns) > np.pi / 4).mean()) if len(turns) else 0.0

    # Jitter: high-frequency wobble, measured as variability of velocity
    # relative to its own mean.
    jitter = float(velocity.std() / velocity.mean()) if velocity.mean() > 0 else 0.0

    # A segment with no clicks reports zero for both click features. That
    # is a real observation -- this round involved no clicking -- and the
    # variance floor stops it dominating the comparison.
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


# All eleven features are computed and returned for inspection. Only the
# subset in COMPARED_FEATURES is used for template matching.
ALL_FEATURES = [
    "velocity_mean", "velocity_std", "velocity_max",
    "accel_mean", "accel_std",
    "path_efficiency", "curvature", "direction_changes", "jitter",
    "click_dwell", "click_interval",
]

# Selected by backward elimination and validated on five held-out seeds:
#
#   all 11 features    EER 0.151 +/- 0.012
#   six features       EER 0.118 +/- 0.017
#   these four         EER 0.095 +/- 0.012
#
# CAVEAT worth stating plainly: this selection was made on simulated
# pointer traces. The simulator applies substantial per-step velocity
# jitter, which may understate how discriminative real velocity is -- the
# mouse dynamics literature generally treats velocity as a primary
# feature. Once real collected traces exist, this subset should be
# re-derived rather than assumed.
COMPARED_FEATURES = [
    "accel_mean",
    "direction_changes",
    "click_dwell",
    "click_interval",
]

FEATURE_ORDER = COMPARED_FEATURES


def build_template(samples: list[dict]) -> dict | None:
    """Mean and standard deviation per feature across enrollment samples."""
    usable = [s for s in samples if s is not None]
    if len(usable) < MIN_SAMPLES:
        return None

    mat = np.array([[s[f] for f in FEATURE_ORDER] for s in usable], dtype=float)
    mean = mat.mean(axis=0)
    std = mat.std(axis=0, ddof=1) if len(usable) > 1 else np.ones(len(FEATURE_ORDER))

    # Floor the deviation relative to the feature's own scale, since these
    # features differ by orders of magnitude (velocity in px/ms versus
    # efficiency in 0-1).
    #
    # The floor is 20% of the mean rather than 5%. Pointer movement varies
    # substantially between sessions -- posture, whether a trackpad or
    # mouse is in use, screen size, how rushed the user is. Enrollment
    # samples collected in one sitting cannot observe that variation, so a
    # tight measured std makes ordinary later movement look anomalous.
    # Testing with a genuine user produced p=0.90 at the narrower floor.
    floor = np.maximum(np.abs(mean) * 0.20, 1e-6)
    std = np.maximum(std, floor)

    return {
        "n_samples": len(usable),
        "features": FEATURE_ORDER,
        "mean": mean.tolist(),
        "std": std.tolist(),
    }


def distance(template: dict, sample: dict) -> float | None:
    """Scaled Manhattan distance, same form as the keystroke comparison."""
    if template is None or sample is None:
        return None

    obs = np.array([sample[f] for f in template["features"]], dtype=float)
    mean = np.asarray(template["mean"])
    std = np.asarray(template["std"])
    scaled = np.clip(np.abs(obs - mean) / std, 0, MAX_DEVIATION)
    return float(scaled.mean())
