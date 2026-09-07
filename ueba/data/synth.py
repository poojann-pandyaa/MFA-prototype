"""
Agent-based synthetic transaction generator.

Adapted from prajwalnayaka/UEBA data_synthesis.py (MIT) -- see NOTICE.
Domain changed from game administration logs to banking transactions.

Method follows the multi-agent approach of:
  Lopez-Rojas, E.A., Elmir, A., Axelsson, S. (2016). "PaySim: A financial
  mobile money simulator for fraud detection." EMSS 2016.

Why agent-based rather than sampling one distribution: every feature in
this project is computed relative to a user's OWN history. If all users
were drawn from a single distribution they would share a profile, and
"8 sigma above this user's mean" would collapse into "8 sigma above the
population mean". The per-user baseline would be meaningless.

Each user is an agent with distinct habits. Fraudsters are agents too, and
because a fraudster operates on a real victim's account, the anomaly
emerges from the simulation rather than being hand-engineered.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CITIES = [
    "Bangalore", "Mumbai", "Delhi", "Chennai", "Hyderabad",
    "Pune", "Kolkata", "Ahmedabad", "Jaipur", "Kochi",
]

FRAUD_CITIES = ["Lagos", "Moscow", "Jakarta", "Karachi", "Manila"]

CHANNELS = ["WEB", "MOBILE"]


@dataclass
class UserAgent:
    """One simulated customer with stable habits."""

    user_id: str
    typical_amount: float
    amount_sigma: float
    active_start: int
    active_end: int
    home_city: str
    devices: list[str]
    beneficiaries: list[str]
    txns_per_week: float
    channel_pref: str

    def sample_amount(self, rng: np.random.Generator) -> float:
        """Log-normal around this user's own typical amount."""
        return float(
            np.round(
                rng.lognormal(np.log(self.typical_amount), self.amount_sigma), 2
            )
        )

    def sample_hour(self, rng: np.random.Generator) -> int:
        """Mostly inside their active window, occasionally outside."""
        if rng.random() < 0.92:
            return int(rng.integers(self.active_start, self.active_end))
        return int(rng.integers(0, 24))


def build_agents(n_users: int, rng: np.random.Generator) -> list[UserAgent]:
    """
    Create distinct user profiles.

    The spread here matters. Typical amounts range across two orders of
    magnitude, so a value that is routine for one agent is extreme for
    another. That variation is what makes per-user features meaningful.
    """
    agents = []
    for i in range(n_users):
        typical = float(rng.choice([800, 1500, 3000, 4200, 8000, 15000, 45000]))
        start = int(rng.integers(6, 11))
        agents.append(
            UserAgent(
                user_id=f"u_{1000 + i}",
                typical_amount=typical * rng.uniform(0.7, 1.3),
                amount_sigma=float(rng.uniform(0.25, 0.6)),
                active_start=start,
                active_end=start + int(rng.integers(8, 14)),
                home_city=str(rng.choice(CITIES)),
                devices=[f"d_{i}_{k}" for k in range(int(rng.integers(1, 3)))],
                beneficiaries=[
                    f"b_{i}_{k}" for k in range(int(rng.integers(2, 7)))
                ],
                txns_per_week=float(rng.uniform(2.0, 12.0)),
                channel_pref=str(rng.choice(CHANNELS, p=[0.35, 0.65])),
            )
        )
    return agents


def _hash_ip(city: str, device: str) -> str:
    """Stand-in for the salted hash the service computes at runtime."""
    return hashlib.sha256(f"{city}|{device}".encode()).hexdigest()[:32]


def _event(
    agent: UserAgent,
    day: int,
    hour: int,
    minute: int,
    amount: float,
    beneficiary: str,
    device: str,
    city: str,
    event_type: str,
    is_fraud: int,
    pattern: str | None,
) -> dict:
    ts = pd.Timestamp("2026-01-01") + pd.Timedelta(
        days=day, hours=hour, minutes=minute
    )
    return {
        "user_id": agent.user_id,
        "event_type": event_type,
        "amount": amount,
        "beneficiary_id": beneficiary,
        "device_id": device,
        "ip_hash": _hash_ip(city, device),
        "city": city,
        "channel": agent.channel_pref,
        "occurred_at": ts,
        "is_fraud": is_fraud,
        "fraud_pattern": pattern,
    }


def _normal_events(agent, n_days, rng) -> list[dict]:
    """
    Normal activity, including legitimate anomalies.

    Real users travel, buy new phones, and occasionally pay someone new.
    Without these, is_new_device and is_new_location would fire only for
    fraud and become perfectly collinear -- the model would be learning a
    generator artifact rather than behaviour. These also create the false
    positives that make the precision/recall trade-off meaningful.
    """
    rows = []
    known_devices = list(agent.devices)

    for day in range(n_days):
        if rng.random() > agent.txns_per_week / 7.0:
            continue

        # Legitimate travel: away from home for a few days at a time.
        travelling = rng.random() < 0.04
        city = str(rng.choice(CITIES)) if travelling else agent.home_city

        # Legitimate new device: replaces or adds to their set, then
        # becomes part of normal history.
        if rng.random() < 0.01:
            known_devices.append(f"d_n_{agent.user_id}_{day}")

        n = 1 + int(rng.random() < 0.15)
        for _ in range(n):
            # Occasional legitimate new payee.
            if rng.random() < 0.06:
                beneficiary = f"b_n_{agent.user_id}_{day}"
                agent.beneficiaries.append(beneficiary)
            else:
                beneficiary = str(rng.choice(agent.beneficiaries))

            rows.append(
                _event(
                    agent,
                    day,
                    agent.sample_hour(rng),
                    int(rng.integers(0, 60)),
                    agent.sample_amount(rng),
                    beneficiary,
                    str(rng.choice(known_devices)),
                    city,
                    "TRANSFER",
                    0,
                    None,
                )
            )
    return rows


# --------------------------------------------------------------------- #
# Fraud patterns
#
# Difficulty is varied deliberately. If every fraud row triggered all seven
# features, detection would be trivial and a PR-AUC near 0.99 would mean
# nothing. Each pattern below fires a different subset.
# --------------------------------------------------------------------- #


def _amount_spike(agent, n_days, rng) -> list[dict]:
    """Fires: amount_zscore only. The hardest pattern by design."""
    day = int(rng.integers(min(30, n_days // 2), n_days))
    return [
        _event(
            agent,
            day,
            agent.sample_hour(rng),
            int(rng.integers(0, 60)),
            round(agent.typical_amount * rng.uniform(6, 12), 2),
            str(rng.choice(agent.beneficiaries)),
            str(rng.choice(agent.devices)),
            agent.home_city,
            "TRANSFER",
            1,
            "amount_spike",
        )
    ]


def _velocity_burst(agent, n_days, rng) -> list[dict]:
    """Fires: txn_velocity_1h, and mildly amount_zscore."""
    day = int(rng.integers(min(30, n_days // 2), n_days))
    hour = agent.sample_hour(rng)
    dev = str(rng.choice(agent.devices))
    rows = []
    for k in range(int(rng.integers(6, 10))):
        rows.append(
            _event(
                agent,
                day,
                hour,
                k * 2,
                round(agent.typical_amount * rng.uniform(0.8, 2.0), 2),
                str(rng.choice(agent.beneficiaries)),
                dev,
                agent.home_city,
                "TRANSFER",
                1,
                "velocity_burst",
            )
        )
    return rows


def _account_takeover(agent, n_days, rng) -> list[dict]:
    """Fires: new device, new location, new beneficiary, hour, amount."""
    day = int(rng.integers(min(30, n_days // 2), n_days))
    return [
        _event(
            agent,
            day,
            int(rng.integers(1, 5)),
            int(rng.integers(0, 60)),
            round(agent.typical_amount * rng.uniform(3, 8), 2),
            f"b_x_{int(rng.integers(0, 9999))}",
            f"d_x_{int(rng.integers(0, 9999))}",
            str(rng.choice(FRAUD_CITIES)),
            "TRANSFER",
            1,
            "account_takeover",
        )
    ]


def _dormant_reactivation(agent, n_days, rng) -> list[dict]:
    """
    Fires: days_since_last_txn, amount_zscore, new beneficiary.

    The reactivation day is spread across the timeline rather than fixed at
    the end. Placing it only at the end would put every instance of this
    pattern in the test half of a time-based split, so a supervised model
    would never see it during training -- an artifact of the generator
    rather than a property of the problem.
    """
    # Needs room for a dormancy gap before it. On short simulations the
    # gap shrinks rather than the pattern being dropped.
    earliest = min(65, max(2, int(n_days * 0.7)))
    day = int(rng.integers(earliest, n_days))
    return [
        _event(
            agent,
            day,
            agent.sample_hour(rng),
            int(rng.integers(0, 60)),
            round(agent.typical_amount * rng.uniform(4, 9), 2),
            f"b_x_{int(rng.integers(0, 9999))}",
            str(rng.choice(agent.devices)),
            agent.home_city,
            "TRANSFER",
            1,
            "dormant_reactivation",
        ),
        # The dormancy window this event reactivates from is created by
        # suppressing the agent's normal activity; see generate().
    ]


PATTERNS = {
    "amount_spike": _amount_spike,
    "velocity_burst": _velocity_burst,
    "account_takeover": _account_takeover,
    "dormant_reactivation": _dormant_reactivation,
}


def generate(
    n_users: int = 200,
    n_days: int = 90,
    fraud_rate: float = 0.01,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Produce a labelled transaction log.

    fraud_rate is approximate: velocity bursts contribute several rows each,
    so the realised rate is computed and reported by scripts/train.py.
    """
    rng = np.random.default_rng(seed)
    agents = build_agents(n_users, rng)

    rows: list[dict] = []
    for agent in agents:
        rows.extend(_normal_events(agent, n_days, rng))

    df = pd.DataFrame(rows)
    target_fraud_rows = int(len(df) * fraud_rate)

    # Allocate victims per pattern. Velocity bursts emit several rows each,
    # so they need proportionally fewer agents to reach a comparable share.
    # Balancing here matters: the per-pattern breakdown in evaluation is
    # only informative if every pattern has enough instances to measure.
    rows_per_agent = {
        "amount_spike": 1,
        "account_takeover": 1,
        "dormant_reactivation": 1,
        "velocity_burst": 8,
    }
    share = target_fraud_rows / 4
    allocation = {
        p: max(1, int(round(share / n))) for p, n in rows_per_agent.items()
    }

    n_needed = sum(allocation.values())
    if n_needed > len(agents):
        raise ValueError(
            f"fraud_rate {fraud_rate} needs {n_needed} victim agents "
            f"but only {len(agents)} exist"
        )

    victims = rng.choice(len(agents), size=n_needed, replace=False)
    cursor = 0
    for pattern, count in allocation.items():
        for idx in victims[cursor : cursor + count]:
            agent = agents[idx]
            new_rows = PATTERNS[pattern](agent, n_days, rng)

            if pattern == "dormant_reactivation":
                # Create the gap this event reactivates from: remove the
                # agent's normal activity in the 60 days before it.
                event_time = new_rows[0]["occurred_at"]
                window_start = event_time - pd.Timedelta(days=60)
                df = df[
                    ~(
                        (df.user_id == agent.user_id)
                        & (df.occurred_at > window_start)
                        & (df.occurred_at < event_time)
                    )
                ]

            df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        cursor += count

    df = df.sort_values(["user_id", "occurred_at"]).reset_index(drop=True)
    return df


if __name__ == "__main__":
    d = generate()
    print(d.head())
    print(f"\nrows: {len(d)}  fraud: {d.is_fraud.sum()} "
          f"({d.is_fraud.mean():.3%})")
    print(d.fraud_pattern.value_counts(dropna=True))
