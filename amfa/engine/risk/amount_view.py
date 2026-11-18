"""How much does the risk model lose if the system knows each payment only by its amount band? (#1, option A against option B.)

An amount view turns an exact amount into what a system that stores only bands would know: one fixed value per band, so Rs 1,200
and Rs 1,900 look the same. It is applied to every amount the model's inputs are computed from (the payment being decided and the
customer's past payments), only inside the experiment (scenarios.dataset, train.run, scripts/compare_amount_storage.py). The live
decision path never uses it, and a model trained on a banded view is refused by the live scorer (scorer.RiskModel).

  exact    the amount as it is (today's system, option A)
  bands4   the band switch's four bands: Rs 0-2,000, 2,000-10,000, 10,000-1 lakh, 1 lakh and above (option B)
  bands8   eight finer bands with the same four edges among them, to show how the loss changes with coarser bands

A band's value is the middle of the band on a log scale (the geometric mean of its edges). The lowest band has no meaningful
lower edge, so Rs 100 stands in for it; the top band has no upper edge, so Rs 10 lakh does. A zero amount (an account change, which
moves no money) stays zero.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from typing import Callable, Optional

EXACT = "exact"
LOW_FLOOR = 100.0            # rupees: the stand-in lower edge of the lowest band
TOP_CEILING = 10_00_000.0    # rupees: the stand-in upper edge of the top band

# lower edges of every band but the first, in rupees
EDGES = {
    "bands4": (2_000.0, 10_000.0, 1_00_000.0),
    "bands8": (500.0, 1_000.0, 2_000.0, 5_000.0, 10_000.0, 25_000.0, 1_00_000.0),
}
VIEWS = (EXACT, *EDGES)


def representatives(edges: tuple[float, ...]) -> tuple[float, ...]:
    """The value each band is known by: the geometric mean of its edges."""
    lows = (LOW_FLOOR, *edges)
    highs = (*edges, TOP_CEILING)
    return tuple(round(math.sqrt(lo * hi), 2) for lo, hi in zip(lows, highs))


def banded(edges: tuple[float, ...]) -> Callable[[float], float]:
    values = representatives(edges)

    def view(amount: float) -> float:
        if not amount or amount <= 0:
            return 0.0
        return values[bisect_right(edges, amount)]
    return view


def get(name: Optional[str]) -> Optional[Callable[[float], float]]:
    """The view called [name], or None for the exact amounts (no change at all). Unknown names are refused."""
    if name in (None, EXACT):
        return None
    if name not in EDGES:
        raise ValueError(f"unknown amount view {name!r}; one of {', '.join(VIEWS)}")
    return banded(EDGES[name])
