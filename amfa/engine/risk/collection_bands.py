"""The amount bands the band switch collects by and decides by (see docs/rbi-banded-data-collection.md).

Four bands, in paise, each including its lower edge and excluding its upper one:

  0  Rs 0         to Rs 1,999.99     everyday payments
  1  Rs 2,000     to Rs 9,999.99     bills, EMIs, shopping
  2  Rs 10,000    to Rs 99,999.99    rent, fees, larger transfers
  3  Rs 1,00,000  and above          rare, large payments, where almost all the money at risk is

The edges are fixed here, not a bank setting. Moving an edge up would put larger payments into a band whose evidence came from
smaller ones (a band the model may already be trusted on), and would make every row collected so far mean something else.

A payment's band is the higher of its own band and the band of everything sent in the last 24 hours including it, so splitting
a payment into smaller ones never moves it to a lower band. Anything that cannot be read as an amount is the top band: an unknown
is never treated as a small payment.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Optional

# lower edge of bands 1, 2 and 3, in paise
EDGES_MINOR: tuple[int, ...] = (2_000_00, 10_000_00, 1_00_000_00)
TOP: int = len(EDGES_MINOR)                       # band 3
BANDS: tuple[int, ...] = tuple(range(TOP + 1))    # 0, 1, 2, 3
LABELS: tuple[str, ...] = ("₹0–2,000", "₹2,000–10,000", "₹10,000–1 lakh", "₹1 lakh and above")
CURRENCY = "INR"


def _minor(value: object) -> Optional[int]:
    """A non-negative whole number of paise, or None for anything else (bool, float, text, negative)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def band_of(amount_minor: object) -> int:
    """The band of one amount in paise. Unreadable or negative: the top band."""
    m = _minor(amount_minor)
    return TOP if m is None else bisect_right(EDGES_MINOR, m)


def band_for_payment(amount_minor: object, exposure_minor: object) -> int:
    """The band a payment is decided and recorded in: the higher of the payment's own band and the band of the 24-hour total
    (this payment plus everything sent in the 24 hours before it). An unknown total cannot rule out a split payment, so it is
    the top band."""
    return max(band_of(amount_minor), band_of(exposure_minor))


def label(band: int) -> str:
    return LABELS[band]
