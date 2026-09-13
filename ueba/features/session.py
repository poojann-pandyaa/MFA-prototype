"""
Session behaviour.

Deliberately NOT an enrollment dimension. Session behaviour is
longitudinal -- how long your sessions usually run, how many actions you
take, how you pace them. None of that can be captured in a single sitting,
so there is nothing to enroll.

It accumulates instead, the same way the transactional features do, and
carries the same cold-start rule: below a minimum number of observed
sessions, the dimension reports insufficient data rather than a score.
"""

from __future__ import annotations

import numpy as np

# Mathematical floor, not a policy threshold -- the standard deviation
# across sessions is undefined below two. Thin history is handled by
# confidence weighting downstream, not by refusing to score.
MIN_SESSIONS = 2

# mean_interval_s and burst_ratio are excluded from the comparison.
# mean_interval_s is largely determined by duration and action count,
# which are both already present; burst_ratio contributed nothing
# measurable.
#
# Held-out validation across five seeds:
#   all 7 features   EER 0.225 +/- 0.018
#   these 5          EER 0.217 +/- 0.020
#
# The improvement is within one standard deviation, so it is a
# simplification rather than a demonstrated gain. Both features are still
# computed and returned.
ALL_FEATURES = [
    "duration_s", "action_count", "actions_per_min", "mean_interval_s",
    "interval_variance", "idle_ratio", "burst_ratio",
]

FEATURE_ORDER = [
    "duration_s",
    "action_count",
    "actions_per_min",
    "interval_variance",
    "idle_ratio",
]

IDLE_THRESHOLD_S = 30.0
BURST_THRESHOLD_S = 1.0

# Absolute floor on the standard deviation per feature, on that feature's
# own scale. A relative floor alone is not enough: ratios such as
# idle_ratio and burst_ratio are legitimately zero across every enrollment
# sample, which would drive their scaled deviation toward infinity and let
# one feature swamp the whole distance.
STD_FLOOR = {
    "duration_s": 30.0,
    "action_count": 1.0,
    "actions_per_min": 0.2,
    "mean_interval_s": 1.0,
    "interval_variance": 0.05,
    "idle_ratio": 0.05,
    "burst_ratio": 0.05,
}

# Per-feature contributions are clipped. A single extreme feature should
# raise suspicion, not dominate the comparison outright.
MAX_DEVIATION = 10.0


def extract(session: dict) -> dict | None:
    """
    Turn one session into a feature vector.

    session: {"start": epoch_s, "end": epoch_s, "actions": [epoch_s, ...]}
    """
    actions = sorted(session.get("actions", []))
    duration = float(session["end"] - session["start"])

    if duration <= 0 or len(actions) < 2:
        return None

    intervals = np.diff(np.array(actions, dtype=float))

    idle_time = float(intervals[intervals > IDLE_THRESHOLD_S].sum())
    bursts = float((intervals < BURST_THRESHOLD_S).mean())

    return {
        "duration_s": duration,
        "action_count": float(len(actions)),
        "actions_per_min": float(len(actions) / (duration / 60.0)),
        "mean_interval_s": float(intervals.mean()),
        # Coefficient of variation rather than raw variance, so the value
        # is comparable across users with different tempos.
        "interval_variance": float(intervals.std() / intervals.mean())
        if intervals.mean() > 0
        else 0.0,
        "idle_ratio": float(idle_time / duration),
        "burst_ratio": bursts,
    }


def build_profile(sessions: list[dict]) -> dict | None:
    """
    Build a behavioural profile from accumulated sessions.

    Returns None below MIN_SESSIONS -- the caller treats that as
    insufficient data, not as low risk.
    """
    usable = [s for s in sessions if s is not None]
    if len(usable) < MIN_SESSIONS:
        return None

    mat = np.array([[s[f] for f in FEATURE_ORDER] for s in usable], dtype=float)
    mean = mat.mean(axis=0)
    std = mat.std(axis=0, ddof=1)

    relative = np.abs(mean) * 0.05
    absolute = np.array([STD_FLOOR[f] for f in FEATURE_ORDER])
    std = np.maximum(std, np.maximum(relative, absolute))

    return {
        "n_sessions": len(usable),
        "features": FEATURE_ORDER,
        "mean": mean.tolist(),
        "std": std.tolist(),
    }


def distance(profile: dict, sample: dict) -> float | None:
    if profile is None or sample is None:
        return None
    obs = np.array([sample[f] for f in profile["features"]], dtype=float)
    mean = np.asarray(profile["mean"])
    std = np.asarray(profile["std"])
    scaled = np.clip(np.abs(obs - mean) / std, 0, MAX_DEVIATION)
    return float(scaled.mean())
