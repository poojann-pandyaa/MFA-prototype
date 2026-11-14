"""Is the model ready to choose a customer's checks? A test on the customer's own payments, not on the calendar.

A customer who pays fifteen times a day and one who pays twice a week learn at very different speeds in days, but at similar
speeds in evidence. So readiness counts evidence:

  PROVEN  The model has been right about them. For every genuine, verified payment we record whether the model would have
          asked for a face check or more (a false alarm). Over their last WINDOW genuine payments, the Wilson upper bound of that
          rate must be at most MAX_FALSE_ALARM: with few payments the bound is wide, so it takes a clean run to pass; one or two
          misses need more payments to outweigh.
  KNOWN   Their usual amount is pinned down. The 90% interval of the median of their log amounts, about
          1.645 * 1.2533 * spread / sqrt(n), must be within AMOUNT_TOLERANCE (about +-30%). A steady payer passes in a handful
          of payments; one whose amounts swing widely needs dozens. The spread is never taken below SPREAD_FLOOR.
  SPREAD  The evidence comes from at least MIN_ACTIVE_DAYS separate days with a payment (not calendar days: a customer who pays
          once a week needs three weeks, one who pays daily needs three days), so a burst of payments in one sitting, for
          instance by someone farming a new account, cannot buy readiness.

Fraud detection is not part of it: there is no fraud on a customer to measure. That is the shared model's job and is measured
across all customers (see the model card). This decides only when the customer's OWN history is good enough to rely on.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from statistics import median

DAY = 86_400.0
WINDOW = 20
MAX_FALSE_ALARM = 0.25       # upper bound: e.g. 0 misses in 12, or 1 in 20
AMOUNT_TOLERANCE = 0.30      # in log terms: the usual amount known to about +-30%
SPREAD_FLOOR = 0.25
MIN_ACTIVE_DAYS = 3
_Z = 1.96


def wilson_upper(misses: int, n: int) -> float:
    if n == 0:
        return 1.0
    p = misses / n
    centre = p + _Z * _Z / (2 * n)
    margin = _Z * math.sqrt(p * (1 - p) / n + _Z * _Z / (4 * n * n))
    return min(1.0, (centre + margin) / (1 + _Z * _Z / n))


@dataclass
class Tracker:
    """One customer's evidence. Feed it each genuine, verified payment in order; ask ready()."""
    alarms: deque = field(default_factory=lambda: deque(maxlen=WINDOW))
    logs: list = field(default_factory=list)
    days: set = field(default_factory=set)

    def observe(self, amount: float, false_alarm: bool, at: float) -> None:
        self.alarms.append(bool(false_alarm))
        self.logs.append(math.log1p(max(amount, 0.0)))
        self.days.add(int(at // 86_400))

    def proven(self) -> bool:
        return wilson_upper(sum(self.alarms), len(self.alarms)) <= MAX_FALSE_ALARM

    def known(self) -> bool:
        n = len(self.logs)
        if n < 3:
            return False
        m = median(self.logs)
        spread = max(1.4826 * median(abs(v - m) for v in self.logs), SPREAD_FLOOR)
        return 1.645 * 1.2533 * spread / math.sqrt(n) <= AMOUNT_TOLERANCE

    def spread(self) -> bool:
        return len(self.days) >= MIN_ACTIVE_DAYS

    def ready(self) -> bool:
        return self.proven() and self.known() and self.spread()

    def to_dict(self) -> dict:
        return {"alarms": [int(a) for a in self.alarms], "logs": [round(v, 4) for v in self.logs[-200:]], "days": sorted(self.days)[-200:]}

    @classmethod
    def from_dict(cls, d: dict) -> "Tracker":
        t = cls()
        t.alarms.extend(bool(a) for a in d.get("alarms", []))
        t.logs = list(d.get("logs", []))
        t.days = set(int(x) for x in d.get("days", []))
        return t

    def progress(self) -> dict:
        """What is left, in words a customer can follow: how many more payments at least (a lower bound, since a false alarm
        or a swinging amount pushes it out)."""
        n = len(self.logs)
        more = 0
        probe = Tracker.from_dict(self.to_dict())
        while not probe.ready() and more < 200:
            more += 1
            probe.observe(math.expm1(median(probe.logs)) if probe.logs else 1000.0, False, max(probe.days or {0}) * DAY + more * DAY)
        return {"payments": n, "moreAtLeast": more if more < 200 else None, "missing": self.why_not()}

    def why_not(self) -> list[str]:
        return [name for name, ok in (("proven", self.proven()), ("known", self.known()), ("spread", self.spread())) if not ok]
