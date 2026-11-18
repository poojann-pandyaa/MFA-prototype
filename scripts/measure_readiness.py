"""When does the model know a customer well enough? A per-customer test, with no days in it, checked on simulated customers.

    AMFA_ENV=development python scripts/measure_readiness.py 8000                    (about 2 minutes; prints a report)
    AMFA_ENV=development python scripts/measure_readiness.py 8000 --hide-declared    (same customers, sign-up answers hidden)
    ... --model=risk-20261116-031301                        (a model that is not active yet)

For each customer, in the order their payments happen (the way it runs live):
  1. the model scores the payment as if it were deciding;
  2. once the payment turns out genuine, we note whether the model would have asked for a face check or more (a false alarm).
The customer is READY when all three hold (amfa/engine/risk/readiness.py):
  * PROVEN: on their last genuine payments the model's false-alarm rate is low, with confidence (Wilson upper bound),
  * KNOWN: their usual amount is pinned down (the uncertainty of their median shrinks with each payment, faster for steady payers),
  * SPREAD: the evidence comes from more than one sitting (a few separate active days), so a burst cannot buy readiness.
Reports how many payments each kind of customer needs, and what false alarms and fraud look like before and after.
"""
import sys
import time
from collections import defaultdict
sys.path.insert(0, ".")

import numpy as np

from amfa.engine.risk import scorer
from amfa.engine.risk.features import vector
from amfa.engine.risk.readiness import Tracker
from dataclasses import replace

from amfa.engine.risk.features import DAY, HISTORY_DAYS, build, exposure_24h
from amfa.engine.risk.scenarios import START, Row, simulate
from amfa.engine.risk.threat import LEVELS, habit_level, level, raise_for_habit, routine_relief, small_payment_cap
from amfa.engine.risk.train import mid_payee_check

LOW_LINE = 10_000
MODEL = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--model=')), None)    # a model version, to measure one that is not active


def experienced_level(m, r, raw) -> str:
    """What the customer would be asked, the way decide() combines model, reliefs and habit."""
    lvl = level(raw, m.cutoffs)
    lvl = (small_payment_cap(lvl, r.features, payee_check=mid_payee_check(r))[0] if r.exposure < LOW_LINE
           else routine_relief(lvl, r.features)[0])
    return raise_for_habit(lvl, habit_level(r.features.get("habit_surprise"), m.habit_cutoffs))[0]


def fresh_start(n: int, seed: int = 11, hide_declared: bool = False) -> list[Row]:
    """Every simulated customer replayed as if they signed up on day one: nothing before it is known. This gives cold starts
    of every pace (the simulator's own new accounts are few and mostly slow payers)."""
    rows = []
    for c, sims in simulate(n, 150, .15, seed):
        sims = [s for s in sims if s.event.at >= START]
        lo = 0
        for i, s in enumerate(sims):
            e = s.event
            while sims[lo].event.at < e.at - HISTORY_DAYS * DAY:
                lo += 1
            if e.type not in ("TRANSFER", "PAYMENT", "CARD_NOT_PRESENT"):
                continue
            history = [x.event for x in sims[lo:i]]
            prof = replace(s.profile, account_created_at=START)
            if hide_declared:        # the same customers, as if nobody had answered the sign-up questions
                prof = replace(prof, declared_typical=None, declared_month=None, peer=None)
            rows.append(Row(build(e, history, prof), s.label, s.scenario, c.segment, e.at, c.cid, c.arch, c.feed,
                            c.weight, e.amount, c.age, c.age_group, exposure_24h(e, history)))
    return rows


def main(n: int, hide_declared: bool = False) -> None:
    t0 = time.time()
    rows = fresh_start(n, hide_declared=hide_declared)
    m = scorer._load(str(scorer.root() / MODEL)) if MODEL else scorer.active()
    raw = m.raw(np.array([vector(r.features) for r in rows]))
    by = defaultdict(list)
    for r, x in zip(rows, raw):
        by[r.customer].append((r, x))
    print(f"{len(rows)} payments, {len(by)} customers, each seen from their first payment, {time.time() - t0:.0f}s\n")

    tiers = [("under 3 a week", 0, 3), ("3-7 a week", 3, 7), ("7-20 a week", 7, 20), ("20-70 a week", 20, 70), ("70+ a week", 70, 1e9)]
    need, stats = defaultdict(list), defaultdict(lambda: defaultdict(lambda: [0, 0]))   # [hits, total]
    never = defaultdict(int)
    first5 = [0, 0, 0, 0]        # false alarms / payments in a customer's first 5, for those who answered the sign-up questions and those who did not
    days_to = defaultdict(list)
    for cust, seq in by.items():
        seq.sort(key=lambda p: p[0].at)
        span = max((seq[-1][0].at - seq[0][0].at) / 86400, 7)
        pace = len(seq) / span * 7
        tier = next(t for t, lo, hi in tiers if lo <= pace < hi)
        tr = Tracker()
        ready_at = None
        answered = seq[0][0].features.get("amount_vs_declared") is not None
        for i, (r, x) in enumerate(seq):
            phase = "ready" if tr.ready() else "learning"
            alarm = LEVELS.index(experienced_level(m, r, x)) >= 2
            key = "fraud" if r.label else "genuine"
            stats[(tier, phase)][key][0] += alarm
            stats[(tier, phase)][key][1] += 1
            if r.label == 0 and i < 5:
                first5[2 * (not answered)] += alarm
                first5[2 * (not answered) + 1] += 1
            if r.label == 0:            # only genuine, verified payments teach the tracker
                tr.observe(r.amount, alarm, r.at)
            if ready_at is None and tr.ready():
                ready_at = i + 1
                days_to[tier].append((r.at - seq[0][0].at) / 86400)
        (need[tier].append(ready_at) if ready_at else None)
        never[tier] += ready_at is None

    print(f"\nFirst five payments, genuine, would get a face check or more: customers who answered the sign-up questions "
          f"{first5[0] / max(first5[1], 1):.1%} of {first5[1]}; customers who skipped them {first5[2] / max(first5[3], 1):.1%} of {first5[3]}\n")
    print("| Customer pace | Customers | Ready within the run | Payments to ready (median, 90th pct) | Days that took (median) | False alarms learning -> ready | Fraud flagged after ready |")
    print("|---|---|---|---|---|---|---|")
    for t, _, _ in tiers:
        got, total = len(need[t]), len(need[t]) + never[t]
        if not total:
            continue
        p = np.percentile(need[t], [50, 90]).round().astype(int) if need[t] else ["-", "-"]
        fa = lambda ph: stats[(t, ph)]["genuine"]
        fr = stats[(t, "ready")]["fraud"]
        rate = lambda a: f"{a[0] / a[1]:.0%}" if a[1] else "-"
        d = round(float(np.median(days_to[t]))) if days_to[t] else "-"
        print(f"| {t} | {total} | {got / total:.0%} | {p[0]}, {p[1]} | {d} | {rate(fa('learning'))} -> {rate(fa('ready'))} | "
              f"{rate(fr)} of {fr[1]} |")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000, hide_declared="--hide-declared" in sys.argv)
