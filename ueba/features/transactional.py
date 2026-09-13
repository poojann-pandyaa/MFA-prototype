"""
Transactional behaviour: the five dimensions from the earlier phase.

Amount, timing, device, location and beneficiary, computed relative to
each user's own history. Unchanged in substance from the standalone
version; included here so the same service covers both transactional and
interaction behaviour.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

FEATURE_ORDER = [
    "amount_zscore", "txn_velocity_1h", "is_new_device", "is_new_location",
    "hour_unusual", "is_new_beneficiary", "days_since_last_txn",
]

# The mathematical floor, not a policy threshold.
#
# amount_zscore divides by the user's standard deviation, which is
# undefined below two observations. Every earlier event still contributes
# to the profile -- learning begins at event one -- but a score cannot be
# produced until a deviation can be computed.
#
# Everything above this floor is handled by confidence weighting rather
# than by a gate: a two-event baseline scores, but contributes almost
# nothing. See FULL_CONFIDENCE_EVENTS in api/service.py.
MIN_EVENTS = 2


@dataclass
class FeatureVector:
    amount_zscore: float
    txn_velocity_1h: int
    is_new_device: int
    is_new_location: int
    hour_unusual: float
    is_new_beneficiary: int
    days_since_last_txn: float

    def as_dict(self):
        return asdict(self)


def _circular_hour_deviation(hour, past_hours):
    """Hours wrap: 23:00 and 01:00 are two apart, not twenty-two."""
    if len(past_hours) == 0:
        return 0.0
    angles = 2 * np.pi * np.asarray(past_hours) / 24.0
    mx, my = np.cos(angles).mean(), np.sin(angles).mean()
    concentration = np.hypot(mx, my)
    if concentration < 1e-9:
        return 0.0
    mean_angle = np.arctan2(my, mx)
    this = 2 * np.pi * hour / 24.0
    diff = abs(np.arctan2(np.sin(this - mean_angle), np.cos(this - mean_angle)))
    return float(diff * 24.0 / (2 * np.pi) * concentration)


def compute_batch(df: pd.DataFrame, min_events: int = MIN_EVENTS) -> pd.DataFrame:
    """Features for every row using only that row's past. No leakage."""
    df = df.sort_values(["user_id", "occurred_at"]).reset_index(drop=True)
    out = []

    for _, group in df.groupby("user_id", sort=False):
        amounts, hours, times = [], [], []
        devices, cities, benefs = set(), set(), set()

        for row in group.itertuples(index=False):
            n = len(amounts)
            if n >= 2:
                arr = np.asarray(amounts)
                mean, std = arr.mean(), arr.std(ddof=1)
                z = 0.0 if std < 1e-9 and abs(row.amount - mean) < 1e-9 else (
                    10.0 if std < 1e-9 else float((row.amount - mean) / std))
            else:
                z = 0.0

            window = row.occurred_at - pd.Timedelta(hours=1)
            velocity = sum(1 for t in reversed(times) if t >= window)
            gap = ((row.occurred_at - times[-1]).total_seconds() / 86400.0
                   if times else 0.0)

            out.append({
                "amount_zscore": z,
                "txn_velocity_1h": velocity,
                "is_new_device": int(n > 0 and row.device_id not in devices),
                "is_new_location": int(n > 0 and row.city not in cities),
                "hour_unusual": _circular_hour_deviation(row.occurred_at.hour, hours),
                "is_new_beneficiary": int(n > 0 and row.beneficiary_id not in benefs),
                "days_since_last_txn": float(gap),
                "has_history": n >= min_events,
            })

            amounts.append(row.amount); hours.append(row.occurred_at.hour)
            devices.add(row.device_id); cities.add(row.city)
            benefs.add(row.beneficiary_id); times.append(row.occurred_at)

    return pd.concat([df, pd.DataFrame(out, index=df.index)], axis=1)


def compute_one(event: dict, history: list[dict]) -> FeatureVector | None:
    """
    Online path: one event against stored history.

    Returns None only when a deviation is mathematically undefined, not
    when history is merely thin. Thin history is handled downstream by
    confidence weighting.
    """
    if len(history) < MIN_EVENTS:
        return None
    rows = history + [event]
    df = pd.DataFrame(rows)
    df["occurred_at"] = pd.to_datetime(df["occurred_at"], unit="s")
    df["user_id"] = event["user_id"]
    last = compute_batch(df, min_events=0).iloc[-1]
    return FeatureVector(
        amount_zscore=float(last.amount_zscore),
        txn_velocity_1h=int(last.txn_velocity_1h),
        is_new_device=int(last.is_new_device),
        is_new_location=int(last.is_new_location),
        hour_unusual=float(last.hour_unusual),
        is_new_beneficiary=int(last.is_new_beneficiary),
        days_since_last_txn=float(last.days_since_last_txn),
    )
