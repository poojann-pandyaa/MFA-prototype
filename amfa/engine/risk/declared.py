"""What a new customer tells us at sign-up, and what that is worth before they have any history.

Two optional questions with a few answers each: roughly how much they send in a month, and what a typical payment is. A customer who
skips them is simply not judged against a declaration. The answers set where the model starts looking for "unusual" on day one (the
centre of the habit score is pulled towards the typical payment they named, with the weight of PEER_K payments, which their own
payments then outweigh), and two model inputs say how far a payment or the month's total is from what they said. Someone who says
"small" and then moves a lot is a signal by itself; the declaration never lowers a check, it only gives the model something to compare
with until the customer's own history is long enough.
"""
from __future__ import annotations

import math

# rupees; the middle of each band the customer picks from
TYPICAL_BANDS = [300.0, 1_200.0, 5_000.0, 25_000.0, 100_000.0]
MONTH_BANDS = [5_000.0, 22_000.0, 110_000.0, 450_000.0, 1_500_000.0]
TYPICAL_LABELS = ["Up to ₹500", "₹500 to ₹2,000", "₹2,000 to ₹10,000", "₹10,000 to ₹50,000", "More than ₹50,000"]
MONTH_LABELS = ["Under ₹10,000", "₹10,000 to ₹50,000", "₹50,000 to ₹2 lakh", "₹2 to ₹10 lakh", "More than ₹10 lakh"]

PEER_SPREAD = 0.9      # how wide "usual" is for someone we know only by what they said (log-amount units)
PEER_K = 8             # the declaration counts as this many payments, so a customer's own outweigh it after a few weeks


def typical_of(band: int | None) -> float | None:
    return TYPICAL_BANDS[band] if band is not None and 0 <= band < len(TYPICAL_BANDS) else None


def month_of(band: int | None) -> float | None:
    return MONTH_BANDS[band] if band is not None and 0 <= band < len(MONTH_BANDS) else None


def band_of(value: float, ladder: list[float]) -> int:
    """The nearest band, in log terms (the bands are roughly evenly spaced in log amounts)."""
    return min(range(len(ladder)), key=lambda i: abs(math.log1p(value) - math.log1p(ladder[i])))


def peer_prior(typical: float | None) -> tuple[float, float] | None:
    """(centre, spread) of the habit score before any history, from the typical payment the customer named."""
    return None if typical is None else (math.log1p(typical), PEER_SPREAD)
