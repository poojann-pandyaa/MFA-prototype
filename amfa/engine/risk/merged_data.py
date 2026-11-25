"""One dataset from two simulators: the platform's (scenarios.py, 26 attack types) and DemoPay's UPI scams (upi/simulate.py,
13 scam types with phone signals and payee intelligence).

Every row carries the platform's features (features.build, so the model reads both kinds of payment through the same code) and,
optionally, DemoPay's own 46 inputs for the same payment, so DemoPay's model can be scored on exactly the same payments.

The two simulators run on their own clocks; each row's `at` is put on the platform's timeline so one time split applies to both:
DemoPay's train / validation / test days (0-90, 90-120, 120-150) map onto the platform's (0-105, 105-128, 128-150).
DemoPay's training and validation rows carry the label a bank would have recorded (15% of victims never report); its test
rows carry the truth, as in DemoPay's own trainer.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from .features import DAY, HISTORY_DAYS, build, exposure_24h
from .scenarios import SAMPLE, START, Row, simulate
from .upi import convert as C
from .upi import features as UF
from .upi import simulate as US

UPI_SPLIT = (90.0, 120.0)          # DemoPay's train / validation ends (days)
PLATFORM_SPLIT = (105.0, 128.0)    # the platform's (train.TRAIN_END, train.VALID_END)
UPI_GENUINE_KEEP = 0.2             # share of DemoPay's genuine payments kept (weighted back up); every fraud payment is kept
# DemoPay's rows together weigh this share of the platform's in training. Chosen on validation (docs/merged-risk-model.md): at
# equal weight the UPI scams pulled the model away from some of the platform's own patterns; at a quarter it keeps them.
UPI_WEIGHT_SHARE = 0.25
_TAG_ORDER = ["phone_change", "sim_replace", "screen_share", "big_oneoff", "other_user", "collect_from_noncontact",
              "similar_legit_payee", "call_during_payment", "travel", "vpn", "sideload", "new_young_payee", "new_payee"]


@dataclass
class MRow(Row):
    source: str = "platform"           # platform | upi
    family: Optional[str] = None       # UNAUTHORISED, COACHED, PAYEE_RISK, FIRST_PARTY for fraud rows
    incident: Optional[str] = None     # one scam incident's payments share this
    upi_x: Optional[list] = None       # DemoPay's 46 inputs for the same payment (when asked for)


def _upi_day_to_platform(d: float) -> float:
    (a, b), (pa, pb) = UPI_SPLIT, PLATFORM_SPLIT
    if d < a:
        return d * pa / a
    if d < b:
        return pa + (d - a) * (pb - pa) / (b - a)
    return pb + (d - b) * (150.0 - pb) / (US.TOTAL_DAYS - b)


def platform_rows(n_customers: int = 4000, days: int = 150, attack_rate: float = .15, seed: int = 7,
                  upi_inputs: bool = False) -> list[MRow]:
    """scenarios.dataset, row for row (same customers, same sampling), plus the family and, if asked, DemoPay's inputs."""
    rng = random.Random(seed + 1)
    rows: list[MRow] = []
    for c, sims in simulate(n_customers, days, attack_rate, seed):
        lo = 0
        st = UF.UserState() if upi_inputs else None
        for i, s in enumerate(sims):
            e = s.event
            while sims[lo].event.at < e.at - HISTORY_DAYS * DAY:
                lo += 1
            share = 1.0 if s.label else SAMPLE.get(s.scenario, 1.0)
            if e.at >= START and rng.random() < share:
                history = [x.event for x in sims[lo:i]]
                ux = UF.featurize(C.to_upi(e, s.profile), st) if upi_inputs else None
                rows.append(MRow(build(e, history, s.profile), s.label, s.scenario, c.segment, e.at, c.cid, c.arch, c.feed,
                                 c.weight / share, e.amount, c.age, c.age_group, exposure_24h(e, history),
                                 source="platform", family=C.family_of(s.scenario) if s.label else None,
                                 incident=f"{c.cid}:{s.scenario}" if s.label else None, upi_x=ux))
            if upi_inputs:
                st.update(e.at, e.payee, e.amount, e.device)
    return rows


def upi_rows(n_users: int = 6000, seed: int = 7, genuine_keep: float = UPI_GENUINE_KEEP,
             upi_inputs: bool = False) -> list[MRow]:
    """DemoPay's payments as platform rows: every fraud payment and a [genuine_keep] share of genuine ones (weighted back up)."""
    rng = random.Random(seed + 11)
    rows: list[MRow] = []
    for uid, u, evs, unreported in C.upi_streams(n_users, seed):
        st = UF.UserState()
        history = []
        for ts, ev, (y, incident, _stealth, tags, atype) in evs:
            day = (ts - US.T0) / DAY
            e, p, sig = C.to_platform(ts, ev, bool(y), u)
            keep = day >= 0 and (y or rng.random() < genuine_keep)
            if keep:
                recent = [h for h in history if h.at >= ts - HISTORY_DAYS * DAY]
                label = int(y) if day >= UPI_SPLIT[1] else int(y and not unreported.get(incident, False))
                scen = f"upi:{atype}" if y else "upi:" + next((t for t in _TAG_ORDER if t in tags), "routine")
                rows.append(MRow(build(e, recent, p, sig), label, scen, "STANDARD", START + _upi_day_to_platform(day) * DAY,
                                 f"u{seed}-{uid}", f"upi-{u.kind}", "upi", 1.0 if y else 1.0 / genuine_keep, float(ev.amount),
                                 0, "unknown", exposure_24h(e, recent),
                                 source="upi", family=C.family_of(scen) if y else None,
                                 incident=f"u{seed}-{incident}" if y else None,
                                 upi_x=UF.featurize(ev, st) if upi_inputs else None))
            if upi_inputs:
                st.update(ts, ev.payee, ev.amount, ev.device_id)
            history.append(e)
    return rows


def split(rows: list[MRow]) -> tuple:
    """Boolean masks (train, valid, test) on the platform's timeline."""
    import numpy as np
    day = (np.array([r.at for r in rows]) - START) / DAY
    a, b = PLATFORM_SPLIT
    return day < a, (day >= a) & (day < b), day >= b


def balance_weights(w, upi, train_mask):
    """Row weights with DemoPay's training rows rescaled to UPI_WEIGHT_SHARE of the platform's."""
    import numpy as np
    w = np.asarray(w, dtype=float).copy()
    if upi.any() and (~upi & train_mask).any():
        w[upi] *= UPI_WEIGHT_SHARE * w[~upi & train_mask].sum() / w[upi & train_mask].sum()
    return w


def class_balanced(labels):
    """Weights that give every class the same total, so a rare family (bad payee) is not drowned out in the router."""
    import numpy as np
    labels = np.asarray(labels)
    counts = {c: (labels == c).sum() for c in set(labels.tolist())}
    return np.array([len(labels) / (len(counts) * counts[c]) for c in labels.tolist()])

