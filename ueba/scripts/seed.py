"""
Seed a user with realistic transaction and session history.

Without this, demonstrating the system means clicking Submit repeatedly,
which produces a dozen transactions within minutes. The transactional
model was trained on users who transact a few times per week over ninety
days, so rapid-fire clicking looks exactly like the velocity_burst fraud
pattern -- and gets correctly flagged.

That is a true positive against the training distribution and a false
positive against reality. Seeding a plausible history fixes the mismatch
rather than weakening the detector.

    python -m scripts.seed devdeep
    python -m scripts.seed devdeep --events 60 --days 90
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import time

from features import session as se
from store import db

DB_PATH = os.getenv("DB_PATH", "ueba.db")


def seed(user_id: str, n_events: int, n_days: int, city: str, device: str,
         seed_value: int, typical_amount: float | None = None) -> None:
    rng = random.Random(seed_value)
    now = time.time()

    if db.get_user(user_id, DB_PATH) is None:
        raise SystemExit(
            f"user {user_id!r} is not enrolled. Enrol in the browser first, "
            "then seed."
        )

    # A stable spending profile, so amount_zscore has a real baseline
    # rather than one assembled from whatever was typed during testing.
    typical = typical_amount or rng.choice([2500, 4200, 8000, 15000])
    sigma = typical * rng.uniform(0.18, 0.30)
    beneficiaries = [f"b_{i}" for i in range(1, rng.randint(4, 7))]

    spacing = (n_days * 86400) / n_events

    for i in range(n_events):
        # Spread across the window with jitter, so velocity stays low and
        # the dormancy feature sees plausible gaps.
        ts = now - (n_days * 86400) + i * spacing + rng.uniform(-0.3, 0.3) * spacing

        # Keep transactions inside a plausible waking window.
        hour = rng.gauss(14, 3.5)
        ts = ts - (ts % 86400) + max(6, min(22, hour)) * 3600

        amount = round(max(rng.gauss(typical, sigma), 50), 2)

        db.add_event(
            user_id, "TRANSFER", amount,
            rng.choice(beneficiaries), device,
            hashlib.sha256(f"{city}|{device}".encode()).hexdigest()[:32],
            city, "WEB", ts, DB_PATH,
        )

    # Session history, so that dimension leaves cold start too.
    for _ in range(max(n_events // 3, se.MIN_SESSIONS + 3)):
        duration = max(rng.gauss(480, 150), 60)
        n_actions = max(int(rng.gauss(14, 4)), 3)
        actions = sorted(rng.uniform(0, duration) for _ in range(n_actions))
        feats = se.extract({"start": 0.0, "end": duration, "actions": actions})
        if feats:
            db.add_session(user_id, feats, DB_PATH)

    events = len(db.get_events(user_id, DB_PATH))
    sessions = len(db.get_sessions(user_id, DB_PATH))

    print(f"seeded {user_id}")
    print(f"  typical amount   ~{typical:,.0f} (sd {sigma:,.0f})")
    print(f"  beneficiaries    {', '.join(beneficiaries)}")
    print(f"  device / city    {device} / {city}")
    print(f"  events now       {events}")
    print(f"  sessions now     {sessions}")
    print()
    print("IN THE UI, use these values for a NORMAL transaction:")
    print(f"  Amount          {typical:,.0f}   (anything within ~2x is normal)")
    print(f"  Device ID       {device}")
    print(f"  Beneficiary ID  {beneficiaries[0]}")
    print()
    print("Sending a very different amount, an unseen device, or an unseen")
    print("beneficiary will raise the transactional score -- which is the")
    print("point, but makes for a confusing demo if it is unintentional.")
    print()
    print("Both dimensions start in WARMING_UP and reach full confidence at")
    print("40 events and 20 sessions respectively.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("user")
    p.add_argument("--events", type=int, default=45)
    p.add_argument("--days", type=int, default=75)
    p.add_argument("--city", default="Bangalore")
    p.add_argument("--device", default="d_1")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--typical", type=float, default=None,
                   help="typical transaction amount for this user")
    a = p.parse_args()
    seed(a.user, a.events, a.days, a.city, a.device, a.seed, a.typical)
