"""
Feature engine.

Turns (current event + that user's history) into the seven-feature vector
the model consumes.

Two rules that shape everything here:

  1. Every continuous feature is computed RELATIVE TO THE USER'S OWN
     history, never the population. A 50,000 transfer is routine for one
     user and an 8-sigma event for another. That relativity is the method.

  2. Nothing identifying reaches the model. No user id, no city name, no
     rupee amount -- only deviations. This is also the data minimisation
     position under the DPDP Act 2023.

Feature references:
  Whitrow, C. et al. (2009). "Transaction aggregation as a strategy for
    credit card fraud detection." Data Mining and Knowledge Discovery.
    -- rolling-window velocity
  Bahnsen, A.C. et al. (2016). "Feature engineering strategies for credit
    card fraud detection." Expert Systems with Applications 51:134-142.
    -- periodic encoding of transaction time
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

FEATURE_ORDER = [
    "amount_zscore",
    "txn_velocity_1h",
    "is_new_device",
    "is_new_location",
    "hour_unusual",
    "is_new_beneficiary",
    "days_since_last_txn",
]

MIN_EVENTS = 10


@dataclass
class FeatureVector:
    amount_zscore: float
    txn_velocity_1h: int
    is_new_device: int
    is_new_location: int
    hour_unusual: float
    is_new_beneficiary: int
    days_since_last_txn: float

    def as_dict(self) -> dict:
        return asdict(self)

    def as_row(self) -> list[float]:
        d = asdict(self)
        return [float(d[k]) for k in FEATURE_ORDER]


def _circular_hour_deviation(hour: int, past_hours: np.ndarray) -> float:
    """
    Hours are circular: 23:00 and 01:00 are two hours apart, not twenty-two.

    Encoding the hour on the unit circle and measuring distance from the
    user's mean direction handles the wraparound. This follows the periodic
    time encoding in Bahnsen et al. (2016).
    """
    if len(past_hours) == 0:
        return 0.0

    angles = 2 * np.pi * past_hours / 24.0
    mean_x, mean_y = np.cos(angles).mean(), np.sin(angles).mean()

    concentration = np.hypot(mean_x, mean_y)
    if concentration < 1e-9:
        # No preferred hour at all; nothing to deviate from.
        return 0.0

    mean_angle = np.arctan2(mean_y, mean_x)
    this_angle = 2 * np.pi * hour / 24.0

    diff = np.abs(np.arctan2(
        np.sin(this_angle - mean_angle), np.cos(this_angle - mean_angle)
    ))
    hours_away = diff * 24.0 / (2 * np.pi)

    # Scale by how tightly the user keeps to a schedule. A user with fixed
    # hours gets a larger score for the same displacement than one who
    # transacts at all times.
    return float(hours_away * concentration)


def compute_batch(df: pd.DataFrame, min_events: int = MIN_EVENTS) -> pd.DataFrame:
    """
    Compute features for every row using only that row's PAST.

    Processed per user in time order, accumulating history as it goes. No
    future information is used for any row -- otherwise the evaluation
    would be measuring leakage rather than detection.

    Returns the input frame with feature columns and `has_history` added.
    """
    df = df.sort_values(["user_id", "occurred_at"]).reset_index(drop=True)
    out: list[dict] = []

    for _, group in df.groupby("user_id", sort=False):
        amounts: list[float] = []
        hours: list[int] = []
        devices: set[str] = set()
        cities: set[str] = set()
        beneficiaries: set[str] = set()
        times: list[pd.Timestamp] = []

        for row in group.itertuples(index=False):
            n = len(amounts)

            # ---- amount deviation -------------------------------------
            if n >= 2:
                arr = np.asarray(amounts)
                mean, std = arr.mean(), arr.std(ddof=1)
                # Zero variance means every prior amount was identical.
                # Fall back to a relative measure rather than dividing by
                # zero or silently reporting 0.
                if std < 1e-9:
                    z = 0.0 if abs(row.amount - mean) < 1e-9 else 10.0
                else:
                    z = float((row.amount - mean) / std)
            else:
                z = 0.0

            # ---- velocity ---------------------------------------------
            window_start = row.occurred_at - pd.Timedelta(hours=1)
            velocity = sum(1 for t in reversed(times) if t >= window_start)

            # ---- novelty ----------------------------------------------
            new_device = int(n > 0 and row.device_id not in devices)
            new_city = int(n > 0 and row.city not in cities)
            new_benef = int(n > 0 and row.beneficiary_id not in beneficiaries)

            # ---- hour -------------------------------------------------
            hour_dev = _circular_hour_deviation(
                row.occurred_at.hour, np.asarray(hours)
            )

            # ---- dormancy ---------------------------------------------
            if times:
                gap = (row.occurred_at - times[-1]).total_seconds() / 86400.0
            else:
                gap = 0.0

            out.append(
                {
                    "amount_zscore": z,
                    "txn_velocity_1h": velocity,
                    "is_new_device": new_device,
                    "is_new_location": new_city,
                    "hour_unusual": hour_dev,
                    "is_new_beneficiary": new_benef,
                    "days_since_last_txn": float(gap),
                    "has_history": n >= min_events,
                }
            )

            amounts.append(row.amount)
            hours.append(row.occurred_at.hour)
            devices.add(row.device_id)
            cities.add(row.city)
            beneficiaries.add(row.beneficiary_id)
            times.append(row.occurred_at)

    features = pd.DataFrame(out, index=df.index)
    return pd.concat([df, features], axis=1)


class FeatureEngine:
    """
    Online path: compute features for one event against stored history.

    Used by the service. Reuses the same arithmetic as compute_batch so
    training and serving cannot drift apart.
    """

    def __init__(self, min_events: int = MIN_EVENTS):
        self.min_events = min_events

    def has_sufficient_history(self, history: list[dict]) -> bool:
        return len(history) >= self.min_events

    def compute(self, event: dict, history: list[dict]) -> FeatureVector:
        if not history:
            return FeatureVector(0.0, 0, 0, 0, 0.0, 0, 0.0)

        hist = pd.DataFrame(history)
        hist["occurred_at"] = pd.to_datetime(hist["occurred_at"])
        ev = dict(event)
        ev["occurred_at"] = pd.to_datetime(ev["occurred_at"])

        combined = pd.concat([hist, pd.DataFrame([ev])], ignore_index=True)
        combined["user_id"] = ev["user_id"]
        result = compute_batch(combined, self.min_events)

        last = result.iloc[-1]
        return FeatureVector(
            amount_zscore=float(last.amount_zscore),
            txn_velocity_1h=int(last.txn_velocity_1h),
            is_new_device=int(last.is_new_device),
            is_new_location=int(last.is_new_location),
            hour_unusual=float(last.hour_unusual),
            is_new_beneficiary=int(last.is_new_beneficiary),
            days_since_last_txn=float(last.days_since_last_txn),
        )
