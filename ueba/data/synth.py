"""
Agent-based transactional generator (carried from the earlier phase).

Each user is an agent with distinct habits, so per-user features are
meaningful. Legitimate anomalies -- travel, device changes, new payees --
are included in normal behaviour so the novelty features do not become
collinear with fraud.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

CITIES = ["Bangalore","Mumbai","Delhi","Chennai","Hyderabad","Pune","Kolkata","Jaipur"]
FRAUD_CITIES = ["Lagos","Moscow","Jakarta","Karachi","Manila"]


@dataclass
class UserAgent:
    user_id: str
    typical_amount: float
    amount_sigma: float
    active_start: int
    active_end: int
    home_city: str
    devices: list
    beneficiaries: list
    txns_per_week: float
    channel: str

    def sample_amount(self, rng):
        return float(np.round(rng.lognormal(np.log(self.typical_amount), self.amount_sigma), 2))

    def sample_hour(self, rng):
        if rng.random() < 0.92:
            return int(rng.integers(self.active_start, self.active_end))
        return int(rng.integers(0, 24))


def build_agents(n, rng):
    agents = []
    for i in range(n):
        typical = float(rng.choice([800,1500,3000,4200,8000,15000,45000]))
        start = int(rng.integers(6, 11))
        agents.append(UserAgent(
            f"u_{1000+i}", typical*rng.uniform(0.7,1.3), float(rng.uniform(0.25,0.6)),
            start, start+int(rng.integers(8,14)), str(rng.choice(CITIES)),
            [f"d_{i}_{k}" for k in range(int(rng.integers(1,3)))],
            [f"b_{i}_{k}" for k in range(int(rng.integers(2,7)))],
            float(rng.uniform(2,12)), str(rng.choice(["WEB","MOBILE"], p=[.35,.65]))))
    return agents


def _hash(city, dev):
    return hashlib.sha256(f"{city}|{dev}".encode()).hexdigest()[:32]


def _ev(a, day, hour, minute, amt, ben, dev, city, fraud, pattern):
    ts = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day, hours=hour, minutes=minute)
    return {"user_id": a.user_id, "event_type": "TRANSFER", "amount": amt,
            "beneficiary_id": ben, "device_id": dev, "ip_hash": _hash(city, dev),
            "city": city, "channel": a.channel, "occurred_at": ts,
            "is_fraud": fraud, "fraud_pattern": pattern}


def _normal(a, n_days, rng):
    rows, devices = [], list(a.devices)
    for day in range(n_days):
        if rng.random() > a.txns_per_week/7.0:
            continue
        city = str(rng.choice(CITIES)) if rng.random() < 0.04 else a.home_city
        if rng.random() < 0.01:
            devices.append(f"d_n_{a.user_id}_{day}")
        for _ in range(1 + int(rng.random() < 0.15)):
            if rng.random() < 0.06:
                ben = f"b_n_{a.user_id}_{day}"; a.beneficiaries.append(ben)
            else:
                ben = str(rng.choice(a.beneficiaries))
            rows.append(_ev(a, day, a.sample_hour(rng), int(rng.integers(0,60)),
                            a.sample_amount(rng), ben, str(rng.choice(devices)),
                            city, 0, None))
    return rows


def _spike(a, n, rng):
    d = int(rng.integers(min(30, n//2), n))
    return [_ev(a, d, a.sample_hour(rng), int(rng.integers(0,60)),
                round(a.typical_amount*rng.uniform(6,12),2),
                str(rng.choice(a.beneficiaries)), str(rng.choice(a.devices)),
                a.home_city, 1, "amount_spike")]


def _burst(a, n, rng):
    d = int(rng.integers(min(30, n//2), n)); h = a.sample_hour(rng)
    dev = str(rng.choice(a.devices))
    return [_ev(a, d, h, k*2, round(a.typical_amount*rng.uniform(0.8,2.0),2),
                str(rng.choice(a.beneficiaries)), dev, a.home_city, 1, "velocity_burst")
            for k in range(int(rng.integers(6,10)))]


def _takeover(a, n, rng):
    d = int(rng.integers(min(30, n//2), n))
    return [_ev(a, d, int(rng.integers(1,5)), int(rng.integers(0,60)),
                round(a.typical_amount*rng.uniform(3,8),2),
                f"b_x_{int(rng.integers(0,9999))}", f"d_x_{int(rng.integers(0,9999))}",
                str(rng.choice(FRAUD_CITIES)), 1, "account_takeover")]


def _dormant(a, n, rng):
    earliest = min(65, max(2, int(n*0.7)))
    d = int(rng.integers(earliest, n))
    return [_ev(a, d, a.sample_hour(rng), int(rng.integers(0,60)),
                round(a.typical_amount*rng.uniform(4,9),2),
                f"b_x_{int(rng.integers(0,9999))}", str(rng.choice(a.devices)),
                a.home_city, 1, "dormant_reactivation")]


PATTERNS = {"amount_spike": _spike, "velocity_burst": _burst,
            "account_takeover": _takeover, "dormant_reactivation": _dormant}


def generate(n_users=200, n_days=90, fraud_rate=0.01, seed=42):
    rng = np.random.default_rng(seed)
    agents = build_agents(n_users, rng)
    rows = []
    for a in agents:
        rows.extend(_normal(a, n_days, rng))
    df = pd.DataFrame(rows)

    per_agent = {"amount_spike":1, "account_takeover":1, "dormant_reactivation":1,
                 "velocity_burst":8}
    share = int(len(df)*fraud_rate)/4
    alloc = {p: max(1, int(round(share/k))) for p, k in per_agent.items()}
    victims = rng.choice(len(agents), size=sum(alloc.values()), replace=False)

    cur = 0
    for pattern, count in alloc.items():
        for idx in victims[cur:cur+count]:
            a = agents[idx]
            new = PATTERNS[pattern](a, n_days, rng)
            if pattern == "dormant_reactivation":
                t = new[0]["occurred_at"]
                df = df[~((df.user_id==a.user_id) &
                          (df.occurred_at > t-pd.Timedelta(days=60)) &
                          (df.occurred_at < t))]
            df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
        cur += count

    return df.sort_values(["user_id","occurred_at"]).reset_index(drop=True)
