"""Simulated customers, genuine edge cases and attacks, for training the risk model before real
labelled outcomes exist.

What is covered (SCENARIO_DOCS lists every tag; export.py reports how many rows each has):
  * 14 kinds of customer: salaried, night shift, student, small business, infrequent, frequent
    traveller, NRI, commuter on mobile data, VPN user, many devices, senior with family help, corporate
    API payer, high-value, gig worker.
  * 47 genuine situations, many of which look like fraud: a new phone while travelling, a real SIM change,
    a forgotten password, a big one-off purchase, a first payment on a new account, a long-dormant account
    returning, failed checks, a wrongly flagged payee, a wedding, a payroll run, rent and premiums that
    repeat monthly, quarterly, half-yearly or yearly ...
  * 25 attacks: takeover, SIM swap, scams (including the same scammer paid again and again), remote
    access, mules, bots, card testing, account-setting abuse (beneficiary / limit / device binding /
    contact), impossible travel, attackers who copy the customer, stolen phones, bust-outs, new-account
    fraud, flagged payees, a regular bill paid into a swapped account ...
  * 6 customer age groups with their own behaviour, attack exposure and payment sizes; age is a dataset
    column for coverage and fairness checks, never a model input.
  * 5 data feeds, because banks will not all send everything: full, no GeoIP, no account events,
    behaviour service down, minimal.

Limits: rates and amounts are invented, and signals the system has no input for (a call in progress, a
screen-sharing app, what the SMS said) cannot appear. Attacks that are indistinguishable from the
customer in every feature (e.g. a customer disputing their own payment) are not simulated. Retrain on
real labelled outcomes as soon as they exist."""
from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable, Iterator, NamedTuple, Optional

from .features import DAY, H, HISTORY_DAYS, Event, Profile, build, exposure_24h

START = 1_767_225_600.0  # 2026-01-01T00:00Z; rows are only taken from START on
WARMUP_DAYS = 365        # established customers already have a year of history at START: all the model remembers
CITIES = [("IN", c) for c in (
    "Mumbai", "Delhi", "Bengaluru", "Chennai", "Hyderabad", "Pune", "Kolkata", "Jaipur", "Ahmedabad",
    "Lucknow", "Surat", "Kochi", "Indore", "Bhopal", "Patna", "Chandigarh", "Nagpur", "Coimbatore",
    "Guwahati", "Bhubaneswar")]
ABROAD = [("AE", "Dubai"), ("SG", "Singapore"), ("GB", "London"), ("US", "New York"), ("NG", "Lagos"),
          ("KH", "Phnom Penh"), ("RU", "Moscow"), ("TH", "Bangkok")]
SEGMENTS = {"STANDARD": .60, "PREMIUM": .12, "SENIOR": .12, "NEW_TO_BANK": .10, "CORPORATE": .06}
MEDIAN_AMOUNT = {"STANDARD": 1500, "PREMIUM": 8000, "SENIOR": 2500, "NEW_TO_BANK": 1000, "CORPORATE": 40000}
TXNS_PER_DAY = {"STANDARD": .7, "PREMIUM": .9, "SENIOR": .4, "NEW_TO_BANK": .5, "CORPORATE": 2.5}
PAY_TYPES = ["TRANSFER", "PAYMENT", "CARD_NOT_PRESENT"]
# Customer age is simulated so behaviour, attacks and fairness can be checked per age group. It is NOT a
# model input: age-based decisions are a fairness and data-protection risk. Multipliers are vs a 35-44 baseline.
class AgeTraits(NamedTuple):
    span: tuple[int, int]       # youngest and oldest age in the group
    rate: float                 # how often they pay, relative to the 35-44 baseline
    median: float               # usual payment size, relative to the baseline
    bound: float                # share whose phone is key-bound
    enrolled: tuple[int, int]   # how many factors they have enrolled
    assisted: float             # chance a payment is made with a family member's help
    micro: float                # share of payments under Rs 100
    phone: float                # chance of changing phone during the period
    web: float                  # chance the main device is the web, not a phone
    late: float                 # hours later (or earlier) than the baseline in the day they pay
    risk: float                 # how often they are targeted, relative to the baseline


AGE_GROUPS = {
    "18-24": AgeTraits((18, 24), 1.4, .35, .80, (3, 4), 0, .30, .20, 0, 2.5, 1.1),
    "25-34": AgeTraits((25, 34), 1.2, .90, .75, (2, 4), 0, .15, .14, .02, 1, 1.0),
    "35-44": AgeTraits((35, 44), 1.0, 1.3, .65, (2, 4), 0, .08, .10, .05, 0, .9),
    "45-59": AgeTraits((45, 59), .85, 1.5, .50, (1, 4), 0, .05, .08, .10, 0, .9),
    "60-74": AgeTraits((60, 74), .5, 1.0, .35, (1, 3), .08, .04, .06, .30, -2, 1.2),
    "75+": AgeTraits((75, 92), .25, .8, .10, (1, 2), .25, .03, .04, .55, -2, 1.5),
}
AGE_BY_SEGMENT = {
    "SENIOR": {"60-74": .8, "75+": .2},
    "NEW_TO_BANK": {"18-24": .35, "25-34": .35, "35-44": .15, "45-59": .10, "60-74": .05},
    "CORPORATE": {"25-34": .2, "35-44": .4, "45-59": .4},
    "STANDARD": {"18-24": .12, "25-34": .30, "35-44": .28, "45-59": .24, "60-74": .06},
}
AGE_BY_SEGMENT["PREMIUM"] = AGE_BY_SEGMENT["STANDARD"]
AGE_BY_ARCHETYPE = {"student": {"18-24": 1}, "gig_worker": {"18-24": .4, "25-34": .4, "35-44": .2},
                    "night_shift": {"18-24": .2, "25-34": .4, "35-44": .3, "45-59": .1}}
# who is targeted: students are recruited as mules, older people are targeted by phone scams
AGE_ATTACK_BIAS = {"18-24": {"customer_as_mule": 4, "card_testing": 1.5, "credential_stuffing": 1.5},
                   "60-74": {"scam_victim": 2, "remote_access": 2, "repeat_scam": 2},
                   "75+": {"scam_victim": 3, "remote_access": 3, "repeat_scam": 3, "profile_change_takeover": 1.5}}
# older customers transact rarely, so they are simulated k times over and each copy counts 1/k in the
# weights: real proportions are unchanged, but their behaviour and fairness can actually be measured
AGE_OVERSAMPLE = {"60-74": 2, "75+": 5}
AMOUNT_BANDS = [(0, 100, "<100"), (100, 1_000, "100-1k"), (1_000, 10_000, "1k-10k"), (10_000, 100_000, "10k-1L"),
                (100_000, 1_000_000, "1L-10L"), (1_000_000, 10_000_000, "10L-1Cr"), (10_000_000, float("inf"), ">1Cr")]


def amount_band(x: float) -> str:
    return next(name for lo, hi, name in AMOUNT_BANDS if lo <= x < hi)

# banks differ in what they send: (GeoIP place, account events + payee flag, behaviour score)
FEEDS = {"full": .55, "no_geo": .15, "no_events": .20, "no_ueba": .05, "minimal": .05}
FEED_HAS = {"full": (1, 1, 1), "no_geo": (0, 1, 1), "no_events": (1, 0, 1), "no_ueba": (1, 1, 0),
            "minimal": (0, 0, 0)}
# share of rows kept per genuine scenario (kept rows carry weight 1/share); everything else is kept whole
SAMPLE = {"routine": .15, "micro_payment": .15, "new_payee": .3, "new_network": .3, "odd_hour": .4,
          "high_behaviour_risk": .5, "bill_burst": .4, "recurring": .3}


# repeating payments: period in days -> (scenario tag, size as a multiple of the customer's usual payment,
# how much the amount varies). Rent and premiums are fixed; utility bills vary.
RECURRING = {30: ("recurring", (2, 8), (0, .25)), 91: ("recurring_quarterly", (4, 15), (0, .1)),
             182: ("recurring_half_yearly", (8, 25), (0, .05)), 365: ("recurring_yearly", (10, 40), (0, 0))}
PERIOD_YOUNG = {30: .55, 91: .15, 182: .15, 365: .15}   # rent, EMIs, subscriptions
PERIOD_OLDER = {30: .30, 91: .20, 182: .25, 365: .25}   # insurance, property tax, medicines, pension-linked


@dataclass(frozen=True)
class Recurring:
    payee: str
    period: int
    amount: float
    cv: float
    offset: float
    tag: str


@dataclass
class Sim:
    event: Event
    profile: Profile
    label: int
    scenario: str


@dataclass
class Row:
    features: dict
    label: int
    scenario: str
    segment: str
    at: float
    customer: str
    archetype: str
    feed: str
    weight: float
    amount: float      # rupees; dataset attributes below are not model inputs
    age: int
    age_group: str
    exposure: float    # rupees moved or attempted in the last 24 h, including this payment


def _pick(rng: random.Random, weights: dict) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _quota(counts: Counter, weights: dict) -> str:
    """Deterministic weighted pick: the option furthest behind its share. Guarantees every option shows
    up in proportion once enough picks have been made, which random draws do not for rare ones."""
    n, total = sum(counts[k] for k in weights) + 1, sum(weights.values())
    k = max(weights, key=lambda k: weights[k] / total * n - counts[k])
    counts[k] += 1
    return k


def _inside(t: float, window: Optional[tuple[float, float]]) -> bool:
    return window is not None and window[0] <= t < window[1]


# ---------- customer types ----------

def _night_shift(c, r): c.peak, c.spread = r.uniform(0, 4), 1.5
def _student(c, r):
    c.median *= .25; c.rate *= 1.8; c.sigma = r.uniform(.8, 1.2); c.payees = c.payees[:r.randint(2, 6)]
def _business(c, r):
    c.rate *= 1.2; c.median *= 2.5; c.sigma = r.uniform(1.0, 1.5); c.burst = .15; c.new_payee_p = .15
    c.payees = [f"{c.cid}-p{k}" for k in range(r.randint(15, 40))]; c.devices.append((f"{c.cid}-d2", "WEB"))
def _infrequent(c, r): c.rate = r.uniform(.03, .12)
def _traveller(c, r): c.trips_n, c.abroad_p = r.randint(3, 5), .5
def _nri(c, r): c.home, c.trips_n = r.choice(ABROAD), r.randint(0, 2)
def _commuter(c, r): c.net_churn = .7
def _vpn(c, r): c.vpn = .5
def _multi_device(c, r):
    c.devices = [(f"{c.cid}-d{k}", r.choice(["MOBILE", "WEB"])) for k in range(r.randint(3, 5))]
def _senior_low_digital(c, r):
    c.rate = r.uniform(.05, .2); c.enrolled = r.randint(1, 2); c.key_bound = False; c.median *= .7
    c.peak, c.spread = r.uniform(10, 12), 1.5; c.payees = c.payees[:r.randint(2, 5)]; c.assisted = .15
    c.devices = [(f"{c.cid}-d0", r.choice(["WEB", "MOBILE"]))]
def _erp(c, r):
    c.devices = [(f"{c.cid}-erp", "API")]; c.peak, c.spread = 13, 2.5; c.rate *= .6; c.median *= 3
    c.burst = .1; c.key_bound = True
def _high_value(c, r): c.median *= 6; c.sigma = r.uniform(.8, 1.3)
def _gig(c, r): c.rate *= 2.5; c.median *= .5; c.peak, c.spread = r.uniform(18, 23), 3.5


@dataclass(frozen=True)
class Archetype:
    weight: float
    tweak: Optional[Callable] = None
    segments: Optional[dict] = None


ARCHETYPES = {
    "salaried": Archetype(.36), "night_shift": Archetype(.04, _night_shift),
    "student": Archetype(.05, _student, {"NEW_TO_BANK": .3, "STANDARD": .7}),
    "small_business": Archetype(.06, _business, {"CORPORATE": .4, "STANDARD": .35, "PREMIUM": .25}),
    "infrequent": Archetype(.08, _infrequent), "frequent_traveller": Archetype(.05, _traveller),
    "nri": Archetype(.04, _nri, {"STANDARD": .5, "PREMIUM": .5}), "commuter": Archetype(.06, _commuter),
    "vpn_user": Archetype(.02, _vpn), "multi_device": Archetype(.05, _multi_device),
    "senior_low_digital": Archetype(.06, _senior_low_digital, {"SENIOR": 1}),
    "corporate_erp": Archetype(.04, _erp, {"CORPORATE": 1}),
    "high_value": Archetype(.04, _high_value, {"PREMIUM": .7, "STANDARD": .3}),
    "gig_worker": Archetype(.05, _gig),
}

PRIORITY = ["false_spoof", "assisted_payment", "flagged_payee_fp", "vpn_noise", "after_sim_change",
            "after_password_reset", "after_contact_change", "dormant_return", "failed_checks", "new_phone",
            "travel_international", "travel_domestic", "recurring", "recurring_quarterly", "recurring_half_yearly",
            "recurring_yearly", "large_purchase", "odd_hour", "cold_start",
            "micro_payment", "new_payee", "high_behaviour_risk", "new_network", "bill_burst"]


def _tag(causes: set) -> str:
    if "new_phone" in causes and causes & {"travel_domestic", "travel_international"}:
        return "new_phone_travel"
    return next((t for t in PRIORITY if t in causes), "routine")


class Customer:
    def __init__(self, cid: str, rng: random.Random, days: int):
        self.cid, self.rng, self.days, self.weight = cid, rng, days, 1.0
        self.arch = _pick(rng, {k: a.weight for k, a in ARCHETYPES.items()})
        spec = ARCHETYPES[self.arch]
        seg_weights = spec.segments or SEGMENTS
        if self.arch in AGE_BY_ARCHETYPE:  # these types are young, so never in the senior segment
            seg_weights = {k: v for k, v in seg_weights.items() if k != "SENIOR"}
        self.segment = _pick(rng, seg_weights)
        self.age_group = _pick(rng, AGE_BY_ARCHETYPE.get(self.arch) or AGE_BY_SEGMENT[self.segment])
        t = AGE_GROUPS[self.age_group]
        self.age = rng.randint(*t.span)
        self.feed = _pick(rng, FEEDS)
        self.median = MEDIAN_AMOUNT[self.segment] * rng.lognormvariate(0, .5) * t.median
        self.sigma = rng.uniform(.5, 1.0)
        self.rate = TXNS_PER_DAY[self.segment] * rng.uniform(.5, 1.6) * t.rate
        self.home = rng.choice(CITIES)
        self.peak = 23.0 if rng.random() < .08 else min(23.0, max(7.0, rng.gauss(15 + t.late, 3)))
        self.spread = rng.uniform(1.5, 4.5)
        self.devices = [(f"{cid}-d0", "WEB" if rng.random() < t.web else "MOBILE")] + (
            [(f"{cid}-d1", "WEB")] if rng.random() < .3 else [])
        self.nets = [f"{cid}-n0", f"{cid}-n1"]
        self.payees = [f"{cid}-p{k}" for k in range(rng.randint(3, 12))]
        self.net_churn, self.vpn, self.assisted, self.burst, self.new_payee_p = .1, 0.0, t.assisted, .04, .09
        self.trips_n, self.abroad_p, self.micro = (1 if rng.random() < .15 else 0), .15, t.micro
        self.enrolled, self.key_bound = rng.randint(*t.enrolled), rng.random() < t.bound
        if spec.tweak:
            spec.tweak(self, rng)
        # nobody has had their account longer than they have been an adult
        self.created = (START + rng.uniform(-60, days * .5) * DAY if self.segment == "NEW_TO_BANK"
                        else START - rng.uniform(60, min(2500, max(120, (self.age - 17) * 365))) * DAY)
        at = lambda p: START + rng.uniform(0, days) * DAY if rng.random() < p else None
        self.sim_at, self.reset_at, self.contact_at = at(.06), at(.10), at(.03)
        # a genuine new phone: after a real SIM change half the time, otherwise on a random day
        self.new_phone_at = self.sim_at if self.sim_at and rng.random() < .5 else at(t.phone)
        self.trips: list = []
        for _ in range(self.trips_n):
            t0 = START + rng.uniform(5, days - 8) * DAY
            self.trips.append((t0, t0 + rng.uniform(3, 8) * DAY, self._destination()))
        if self.trips and rng.random() < .3:  # phone lost, broken or replaced while away
            self.new_phone_at = rng.uniform(self.trips[0][0], self.trips[0][1])
        self.silence = None  # a genuine long absence, after which the customer returns
        if self.created < START - 250 * DAY and rng.random() < .04:
            end = START + rng.uniform(20, days - 5) * DAY
            self.silence = (end - rng.uniform(40, 200) * DAY, end)
            if rng.random() < .6:
                self.reset_at = end - rng.uniform(0, 2) * H
            if rng.random() < .3:
                self.new_phone_at = end
        weights = PERIOD_OLDER if self.age >= 60 else PERIOD_YOUNG
        self.recurring: list[Recurring] = []
        for _ in range(rng.choices([0, 1, 2, 3], [.45, .30, .17, .08])[0]):
            period = _pick(rng, weights)
            tag, (lo, hi), (cv_lo, cv_hi) = RECURRING[period]
            self.recurring.append(Recurring(f"{cid}-r{len(self.recurring)}", period, self.median * rng.uniform(lo, hi),
                                            rng.uniform(cv_lo, cv_hi), rng.uniform(0, period), tag))

    def _destination(self):
        r = self.rng
        if self.home[0] != "IN":
            return r.choice(CITIES)
        return r.choice(ABROAD) if r.random() < self.abroad_p else r.choice([x for x in CITIES if x != self.home])

    def open_account(self, at: float, kind: str) -> None:
        """Make this customer an account a fraudster opened shortly before attacking."""
        r = self.rng
        self.segment, self.median = "NEW_TO_BANK", MEDIAN_AMOUNT["NEW_TO_BANK"] * r.lognormvariate(0, .4)
        self.created = at - (r.uniform(30, 70) if kind == "bustout" else r.uniform(.05, 3)) * DAY
        self.enrolled, self.key_bound = r.randint(1, 2), r.random() < .3
        self.sim_at = self.reset_at = self.contact_at = self.new_phone_at = self.silence = None
        self.recurring, self.trips, self.rate = [], [], .5 * r.uniform(.5, 1.6)

    def amt(self) -> float:
        return self.rng.lognormvariate(0, self.sigma) * self.median

    def known(self) -> str:
        return self.rng.choice(self.payees)

    def fresh(self) -> str:
        return f"{self.cid}-n{self.rng.randint(0, 10**9)}"

    def profile(self, at: float, ueba: float, flag: bool, bound: bool, extra: Optional[dict] = None) -> Profile:
        p = Profile(segment=self.segment, account_created_at=self.created, enrolled_factors=self.enrolled,
                    device_key_bound=bound, ueba_risk=round(min(1.0, max(0.0, ueba)), 3),
                    sim_changed_at=self.sim_at if self.sim_at and self.sim_at <= at else None,
                    contact_changed_at=self.contact_at if self.contact_at and self.contact_at <= at else None,
                    password_reset_at=self.reset_at if self.reset_at and self.reset_at <= at else None,
                    payee_risk_flag=flag)
        return replace(p, **(extra or {}))

    def _mk(self, at: float, scenario: str, label: int, *, amount: float, payee: str, type: str = "TRANSFER",
            device: Optional[str] = None, channel: Optional[str] = None, net: Optional[str] = None,
            place: Optional[tuple] = None, authorized: bool = True, failed: int = 0, spoof: bool = False,
            ueba: Optional[tuple] = None, flag: bool = False, bound: Optional[bool] = None,
            extra: Optional[dict] = None) -> Sim:
        """One event. Anything not given is the customer's own phone, network and home."""
        own_dev, own_ch = self.devices[0]
        pl = place or self.home
        e = Event(at=at, hour=int(((at - START) % DAY) // H), amount=round(amount, 2), type=type,
                  channel=channel or own_ch, payee=payee, device=device or own_dev,
                  network=net or self.nets[0], place=f"{pl[0]}:{pl[1]}", country=pl[0],
                  authorized=authorized, failed_factors=failed, spoof=spoof)
        u = self.rng.betavariate(*(ueba or (2, 6)))
        return Sim(e, self.profile(at, u, flag, self.key_bound if bound is None else bound, extra), label, scenario)

    # ---------- genuine behaviour ----------

    def _schedule(self, blackout) -> list[tuple[float, str]]:
        rng, out = self.rng, []
        t, end = max(START - WARMUP_DAYS * DAY, self.created), START + self.days * DAY
        while True:
            t += rng.expovariate(self.rate) * DAY
            if t >= end:
                break
            if _inside(t, blackout) or _inside(t, self.silence):
                continue
            day0 = t - (t - START) % DAY
            at = day0 + (int(rng.gauss(self.peak, self.spread)) % 24) * H + rng.uniform(0, H)
            out.append((at, "base"))
            if rng.random() < self.burst:  # bill-payment day: a burst of payments within the hour
                out += [(at + rng.uniform(1, 50) * 60, "bill_burst") for _ in range(rng.randint(2, 5))]
        # genuine account events are usually followed soon by a payment (the reason they happened)
        for name, ev_at in (("after_password_reset", self.reset_at), ("after_sim_change", self.sim_at),
                            ("after_contact_change", self.contact_at)):
            if ev_at and rng.random() < .7 and not _inside(ev_at, blackout):
                out.append((ev_at + rng.uniform(.2, 24) * H, name))
        for i, rec in enumerate(self.recurring):  # on the due date give or take a day or two, at their usual hour
            t = START - WARMUP_DAYS * DAY + rec.offset * DAY
            while t < end:
                out.append((t + rng.uniform(-1, 1) * DAY + self.peak * H, f"rec{i}"))
                t += rec.period * DAY
        if self.silence:
            out += [(self.silence[1] + rng.uniform(.1, 48) * H * (k + 1), "dormant_return")
                    for k in range(rng.randint(1, 3))]
        return sorted((a, h) for a, h in out if self.created <= a < end and not _inside(a, self.silence)
                      and not (h.startswith("rec") and _inside(a, blackout)))

    def genuine(self, blackout=None) -> list[Sim]:
        rng, out = self.rng, []
        for at, hint in self._schedule(blackout):
            hour = int(((at - START) % DAY) // H)
            causes = set() if hint in ("base", ) or hint.startswith("rec") else {hint}
            dev, channel = rng.choice(self.devices)
            new_device = False
            if self.new_phone_at and at >= self.new_phone_at and channel == "MOBILE":
                dev, new_device = f"{self.cid}-d0b", at - self.new_phone_at < 3 * DAY
                if new_device:
                    causes.add("new_phone")
            trip = next((t for t in self.trips if t[0] <= at < t[1]), None)
            place = trip[2] if trip else self.home
            if trip:
                causes.add("travel_international" if place[0] != self.home[0] else "travel_domestic")
                net = f"{self.cid}-trip{self.trips.index(trip)}"
            elif rng.random() < self.net_churn:
                net = f"{self.cid}-x{rng.randint(0, 10**6)}"
                causes.add("new_network")
            else:
                net = rng.choice(self.nets)
            if rng.random() < self.vpn:
                place, net = rng.choice(CITIES + ABROAD), f"{self.cid}-vpn{rng.randint(0, 10**6)}"
                causes.add("vpn_noise")
            if hint.startswith("rec"):
                rec = self.recurring[int(hint[3:])]
                payee, new_payee = rec.payee, False
                amount = max(rec.amount * .3, rec.amount * (1 + rng.gauss(0, rec.cv)))
                causes.add(rec.tag)
            else:
                amount, new_payee = self.amt(), rng.random() < self.new_payee_p
                micro = hint == "base" and rng.random() < self.micro
                if micro:  # a UPI-size payment: tea, an auto ride, parking
                    amount = rng.uniform(1, 99)
                    causes.add("micro_payment")
                elif rng.random() < .05:  # rent deposit, a phone, a holiday: big and often to someone new
                    amount *= rng.uniform(3, 15)
                    new_payee = new_payee or rng.random() < .5
                    causes.add("large_purchase")
                payee = self.fresh() if new_payee else self.known()
                if new_payee:
                    causes.add("new_payee")
            if len(out) < 3 and self.created > START - WARMUP_DAYS * DAY:
                causes.add("cold_start")
            if self.assisted and rng.random() < self.assisted:  # a family member helps from their own phone
                dev, net, place = f"{self.cid}-fam", f"{self.cid}-famnet", rng.choice(CITIES)
                causes.add("assisted_payment")
            if abs(((hour - self.peak + 12) % 24) - 12) > 6 and hint == "base":
                causes.add("odd_hour")
            failed, authorized = int(rng.random() < .04), rng.random() > .02
            if rng.random() < .01:  # fumbles the face or voice check a few times
                failed, authorized = rng.randint(2, 4), rng.random() < .6
                causes.add("failed_checks")
            spoof = rng.random() < .003
            if spoof:
                failed += 1
                causes.add("false_spoof")
            ueba = (5, 3) if rng.random() < .03 else (3, 5) if new_device or "assisted_payment" in causes else (2, 6)
            if ueba == (5, 3):
                causes.add("high_behaviour_risk")
            flag = new_payee and rng.random() < .03
            if flag:
                causes.add("flagged_payee_fp")
            ttype = ("PAYMENT" if "micro_payment" in causes else
                     "CARD_NOT_PRESENT" if trip and rng.random() < .35 else rng.choices(PAY_TYPES, [.6, .3, .1])[0])
            out.append(self._mk(at, _tag(causes), 0, amount=amount, payee=payee, type=ttype, device=dev,
                                channel=channel, net=net, place=place, authorized=authorized, failed=failed,
                                spoof=spoof, ueba=ueba, flag=flag, bound=self.key_bound and not new_device))
        return out

    def attack(self, kind: str, at: float) -> list[Sim]:
        a = Atk(self, kind, at)
        ATTACK_FN[kind](a)
        return a.out


# ---------- genuine situations, scripted ----------

def _festival(c, at):
    r = c.rng
    return [c._mk(at + r.uniform(0, 5) * DAY, "festival_spend", 0, amount=c.amt() * r.uniform(1.5, 5),
                  payee=c.fresh() if r.random() < .4 else c.known(), type=r.choice(PAY_TYPES))
            for _ in range(r.randint(4, 10))]


def _emergency(c, at):
    r, t = c.rng, at - (at - START) % DAY + c.rng.uniform(0, 5) * H
    return [c._mk(t + k * r.uniform(5, 40) * 60, "emergency_payment", 0, amount=c.median * r.uniform(5, 30),
                  payee=c.fresh(), ueba=(3, 5)) for k in range(r.randint(1, 3))]


def _add_beneficiary(c, at):
    r, p = c.rng, c.fresh()
    big = r.random() < .25  # moving savings to one's own other account
    return [c._mk(at, "genuine_add_beneficiary", 0, amount=0, payee=p, type="ADD_BENEFICIARY"),
            c._mk(at + r.uniform(1, 120) * 60, "after_add_beneficiary", 0, payee=p,
                  amount=c.median * (r.uniform(8, 40) if big else r.uniform(.5, 6)))]


def _limit_change(c, at):
    r = c.rng
    return [c._mk(at, "limit_change", 0, amount=0, payee=f"{c.cid}-limit", type="CHANGE_LIMIT"),
            c._mk(at + r.uniform(.2, 48) * H, "after_limit_change", 0, amount=c.median * r.uniform(3, 12),
                  payee=c.known())]


def _profile_update(c, at):
    r, x = c.rng, {"contact_changed_at": at}
    return [c._mk(at, "profile_update", 0, amount=0, payee=f"{c.cid}-profile", type="PROFILE_CHANGE", extra=x)] + [
        c._mk(at + r.uniform(.3, 30) * H, "after_profile_update", 0, amount=c.amt(), payee=c.known(), extra=x)
        for _ in range(r.randint(0, 2))]


def _device_binding(c, at):
    r, dev = c.rng, f"{c.cid}-bind{c.rng.randint(0, 99)}"
    return [c._mk(at, "genuine_device_binding", 0, amount=0, payee=f"{c.cid}-bind", type="DEVICE_BINDING",
                  device=dev, ueba=(3, 5), bound=False)] + [
        c._mk(at + r.uniform(.2, 72) * H, "after_device_binding", 0, amount=c.amt(), payee=c.known(),
              device=dev, ueba=(3, 5), bound=True) for _ in range(r.randint(1, 3))]


def _split(c, at):  # a limit forces one payment into several
    r, p, n = c.rng, c.fresh(), c.rng.randint(2, 3)
    amount = c.median * r.uniform(4, 15)
    return [c._mk(at + k * r.uniform(1, 20) * 60, "split_payment", 0, amount=amount / n, payee=p) for k in range(n)]


def _test_payment(c, at):  # a rupee or two first, to check the account is right
    r, p = c.rng, c.fresh()
    return [c._mk(at, "test_payment", 0, amount=r.uniform(1, 10), payee=p),
            c._mk(at + r.uniform(2, 60) * 60, "test_payment", 0, amount=c.median * r.uniform(2, 20), payee=p)]


def _payroll(c, at):
    r, t = c.rng, at - (at - START) % DAY + 10 * H
    return [c._mk(t + k * r.uniform(.2, 1.5) * 60, "payroll_batch", 0, amount=c.median * r.uniform(.3, 1.5),
                  payee=c.fresh() if r.random() < .2 else c.known(), channel="API") for k in range(r.randint(10, 25))]


def _assisted(c, at):
    r = c.rng
    return [c._mk(at + k * 600, "assisted_payment", 0, amount=c.amt(),
                  payee=c.known() if r.random() < .6 else c.fresh(), device=f"{c.cid}-fam", channel="MOBILE",
                  net=f"{c.cid}-famnet", place=r.choice(CITIES), ueba=(4, 4), bound=False)
            for k in range(r.randint(1, 2))]


def _first_large(c, at):  # first thing a new customer does is a deposit or a big purchase
    return [c._mk(c.created + c.rng.uniform(.05, 2) * DAY, "cold_start_large", 0,
                  amount=c.median * c.rng.uniform(5, 25), payee=c.fresh(), ueba=(3, 5))]


def _high_value(c, at):  # a car down-payment, jewellery, a fee
    return [c._mk(at, "high_value_purchase", 0, amount=c.median * c.rng.uniform(20, 80), payee=c.fresh(),
                  type="PAYMENT")]


def _wedding(c, at):
    r = c.rng
    return [c._mk(at + r.uniform(0, 2) * DAY, "wedding_vendors", 0, amount=c.median * r.uniform(2, 12),
                  payee=c.fresh()) for _ in range(r.randint(5, 10))]


def _retry(c, at):  # wrong OTP or a bad camera angle, then it works
    r, p, a = c.rng, c.known(), c.amt()
    out = [c._mk(at + k * r.uniform(.5, 4) * 60, "retry_after_failure", 0, amount=a, payee=p, authorized=False,
                 failed=r.randint(1, 2)) for k in range(r.randint(2, 3))]
    return out + [c._mk(out[-1].event.at + r.uniform(1, 5) * 60, "retry_after_failure", 0, amount=a, payee=p)]


def _very_large(c, at):  # Rs 10 lakh to 3 crore: property, insurance premium, advance tax, a deal
    r = c.rng
    return [c._mk(at, "very_large_payment", 0, amount=math.exp(r.uniform(math.log(1e6), math.log(3e7))),
                  payee=c.fresh() if r.random() < .8 else c.known(), ueba=(2, 6))]


def _older_large(c, at):  # a hospital bill, a fixed deposit, a gift to family: Rs 1 to 15 lakh
    r = c.rng
    return [c._mk(at, "older_large_payment", 0, amount=math.exp(r.uniform(math.log(1e5), math.log(1.5e6))),
                  payee=c.fresh() if r.random() < .6 else c.known(), ueba=(2, 6))]


@dataclass(frozen=True)
class Script:
    weight: float
    fn: Callable
    ok: Callable = lambda c: True


SCRIPTS = {
    "festival_spend": Script(.10, _festival), "emergency_payment": Script(.08, _emergency),
    "add_beneficiary": Script(.12, _add_beneficiary), "limit_change": Script(.05, _limit_change),
    "profile_update": Script(.04, _profile_update), "device_binding": Script(.05, _device_binding),
    "split_payment": Script(.06, _split), "test_payment": Script(.06, _test_payment),
    "payroll_batch": Script(.06, _payroll,
                            lambda c: c.segment == "CORPORATE" or c.arch in ("small_business", "corporate_erp")),
    "assisted_payment": Script(.08, _assisted, lambda c: c.segment == "SENIOR"),
    "cold_start_large": Script(.05, _first_large, lambda c: c.segment == "NEW_TO_BANK" and c.created > START),
    "high_value_purchase": Script(.06, _high_value), "wedding_vendors": Script(.04, _wedding),
    "retry_after_failure": Script(.08, _retry),
    "very_large_payment": Script(.05, _very_large, lambda c: c.segment in ("PREMIUM", "CORPORATE")),
    "older_large_payment": Script(.06, _older_large, lambda c: c.age >= 60),
}


# ---------- attacks ----------

class Atk:
    """What an attacker has: their own device, network and a far-away place, and the customer's stats."""

    def __init__(self, c: Customer, kind: str, at: float):
        r = self.rng = c.rng
        self.c, self.kind, self.out = c, kind, []
        self.dev, self.net = f"atk-{c.cid}-{r.randint(0, 9)}", f"atk-net-{r.randint(0, 10**6)}"
        self.far = r.choice([x for x in CITIES if x != c.home] + ABROAD)
        self.at = at - (at - START) % DAY + r.uniform(0, 5) * H if r.random() < .35 else at  # many at night
        self.at = max(self.at, c.created + 60)  # never before the account exists

    def mule(self) -> str:
        return f"mule-{self.rng.randint(0, 10**6)}"

    def big(self, lo: float, hi: float) -> float:
        # the balance of a business or premium account can be drained for crores, not just a multiple of habit
        if hi >= 20 and self.c.segment in ("CORPORATE", "PREMIUM") and self.rng.random() < .15:
            return math.exp(self.rng.uniform(math.log(2e6), math.log(8e7)))
        return self.c.median * self.rng.uniform(lo, hi)

    def own(self) -> dict:  # acting from the customer's own phone, network and home
        dev, ch = self.c.devices[0]
        return dict(device=dev, net=self.c.nets[0], place=self.c.home, channel=ch, bound=self.c.key_bound)

    def ev(self, t, amount, payee, *, device=None, net=None, place=None, channel="MOBILE", type="TRANSFER",
           failed=0, spoof=False, ueba=(5, 3), flag=.2, extra=None, authorized=False, bound=False, label=1,
           tag=None):
        self.out.append(self.c._mk(t, tag or self.kind, label, amount=amount, payee=payee, type=type,
                                   device=device or self.dev, channel=channel, net=net or self.net,
                                   place=place or self.far, authorized=authorized, failed=failed, spoof=spoof,
                                   ueba=ueba, flag=self.rng.random() < flag, bound=bound, extra=extra))


def _a_takeover(a):
    r = a.rng
    extra = ({"password_reset_at": a.at - r.uniform(.5, 24) * H} if r.random() < .5 else
             {"contact_changed_at": a.at - r.uniform(.5, 24) * H} if r.random() < .4 else {})
    place = a.far if r.random() < .6 else a.c.home
    for k in range(r.randint(1, 3)):
        a.ev(a.at + k * r.uniform(2, 30) * 60, a.big(1.5, 30), a.mule(), place=place,
             channel=r.choice(["MOBILE", "WEB"]), failed=int(r.random() < .3), spoof=r.random() < .1,
             extra=extra, flag=.15)


def _a_sim_swap(a):
    r = a.rng
    extra, place = {"sim_changed_at": a.at - r.uniform(1, 24) * H}, a.far if r.random() < .5 else a.c.home
    for k in range(r.randint(2, 4)):
        a.ev(a.at + k * r.uniform(1, 15) * 60, a.big(2, 25), a.mule(), place=place,
             channel="WEB" if r.random() < .3 else "MOBILE", failed=int(r.random() < .2), extra=extra)


def _a_scam(a):  # the real customer, on their own phone, coached into paying a scammer
    r, p = a.rng, a.mule()
    flag = .6 if r.random() < .3 else .2
    for k in range(r.randint(1, 3)):
        a.ev(a.at + k * r.uniform(5, 300) * 60, a.big(1.5, 20), p, ueba=(2.5, 5), flag=flag, **a.own())


def _a_remote_access(a):  # the scammer drives the customer's own phone through a screen-sharing app
    r = a.rng
    for k in range(r.randint(1, 3)):
        a.ev(a.at + k * r.uniform(2, 20) * 60, a.big(2, 15), a.mule(), ueba=(4, 4), failed=int(r.random() < .15),
             **a.own())


def _a_mule_fanout(a):
    r, kw = a.rng, (a.own() if a.rng.random() < .5 else {})
    for k in range(r.randint(3, 6)):
        a.ev(a.at + k * r.uniform(10, 90) * 60, a.big(.8, 4), a.mule(), flag=.3, **kw)


def _a_bot(a):
    r = a.rng
    for k in range(r.randint(3, 8)):
        a.ev(a.at + k * r.uniform(1, 12) * 60, a.big(.5, 3), a.mule() if r.random() < .5 else a.c.known(),
             channel=r.choice(["API", "WEB"]), ueba=(4, 3), flag=.1)


def _a_dormant(a):
    r, extra = a.rng, {"password_reset_at": a.at - a.rng.uniform(1, 48) * H}
    for k in range(r.randint(1, 2)):
        a.ev(a.at + k * r.uniform(5, 60) * 60, a.big(3, 20), a.mule(), place=a.far if r.random() < .5 else a.c.home,
             failed=int(r.random() < .2), extra=extra)


def _a_stuffing(a):  # the password is known, the step-up checks keep failing
    r = a.rng
    for k in range(r.randint(3, 8)):
        a.ev(a.at + k * r.uniform(.2, 3) * 60, a.big(.01, .3), a.mule(), channel=r.choice(["WEB", "API"]),
             failed=r.randint(1, 4), ueba=(6, 2))


def _a_card_testing(a):
    r = a.rng
    for k in range(r.randint(6, 15)):
        a.ev(a.at + k * r.uniform(.2, 3) * 60, r.uniform(1, 60), a.mule(), type="CARD_NOT_PRESENT",
             channel=r.choice(["WEB", "API"]), flag=.1)
    if r.random() < .5:
        a.ev(a.at + 40 * 60, a.big(1, 4), a.mule(), type="CARD_NOT_PRESENT", channel="WEB")


def _a_test_drain(a):
    r, p = a.rng, a.mule()
    a.ev(a.at, r.uniform(1, 10), p, authorized=True)
    a.ev(a.at + r.uniform(3, 120) * 60, a.big(5, 40), p)


def _a_slow_drip(a):  # many payments just under the band, early ones go through and build trust
    r, p = a.rng, a.mule()
    for k in range(r.randint(3, 8)):
        a.ev(a.at + k * r.uniform(.5, 2) * DAY, a.big(.8, 2.5), p, authorized=r.random() < .6, ueba=(3, 5))


def _a_bustout(a):  # an account built up with small payments, then drained
    r = a.rng
    for k in range(r.randint(2, 5)):
        a.ev(a.at + k * r.uniform(5, 90) * 60, a.big(10, 60), a.mule(), ueba=(3, 4), **a.own())


def _a_new_account(a):
    r = a.rng
    for k in range(r.randint(1, 3)):
        a.ev(a.at + k * r.uniform(2, 40) * 60, a.big(10, 80), a.mule())


def _a_beneficiary(a):  # add a payee, then pay it moments later
    r, p = a.rng, a.mule()
    a.ev(a.at, 0, p, type="ADD_BENEFICIARY", authorized=True)
    a.ev(a.at + r.uniform(1, 30) * 60, a.big(3, 30), p)


def _a_limit(a):
    r = a.rng
    a.ev(a.at, 0, f"{a.c.cid}-limit", type="CHANGE_LIMIT", authorized=True)
    for k in range(r.randint(1, 3)):
        a.ev(a.at + (5 + k * r.uniform(2, 20)) * 60, a.big(10, 80), a.mule())


def _a_binding(a):  # bind the attacker's phone, which then counts as trusted
    r, extra = a.rng, {"sim_changed_at": a.at - a.rng.uniform(1, 24) * H}
    a.ev(a.at, 0, f"{a.c.cid}-bind", type="DEVICE_BINDING", authorized=True, extra=extra)
    for k in range(r.randint(1, 3)):
        a.ev(a.at + r.uniform(10, 120) * 60 + k * 600, a.big(3, 30), a.mule(), bound=True, extra=extra)


def _a_profile(a):  # change the contact details so alerts go elsewhere
    r, extra = a.rng, {"contact_changed_at": a.at}
    a.ev(a.at, 0, f"{a.c.cid}-profile", type="PROFILE_CHANGE", authorized=True, extra=extra)
    for k in range(r.randint(1, 3)):
        a.ev(a.at + r.uniform(.2, 6) * H + k * 900, a.big(3, 30), a.mule(), extra=extra)


def _a_impossible(a):  # the customer pays at home; minutes later the account is used far away
    r, c = a.rng, a.c
    a.ev(a.at, c.amt(), c.known(), label=0, tag="impossible_travel_real_leg", authorized=True, ueba=(2, 6), **a.own())
    a.ev(a.at + r.uniform(3, 40) * 60, a.big(3, 30), a.mule())


def _a_mimic(a):  # copies the customer: usual hour, usual size, sometimes their city; only the device is new
    r, c = a.rng, a.c
    t0 = a.at - (a.at - START) % DAY + (int(c.peak) % 24) * H + r.uniform(-2, 2) * H
    kw = {"place": c.home, "channel": c.devices[0][1]} if r.random() < .5 else {}
    for k in range(r.randint(1, 3)):
        a.ev(t0 + k * r.uniform(10, 120) * 60, a.big(.8, 2.5), a.mule() if r.random() < .8 else c.known(),
             ueba=(3, 5), flag=.05, **kw)


def _a_stolen(a):  # the real phone, the wrong person
    r, kw = a.rng, a.own()
    if r.random() < .5:
        kw["place"] = a.far
    for k in range(r.randint(1, 3)):
        a.ev(a.at + k * r.uniform(2, 30) * 60, a.big(3, 40), a.mule(), failed=r.randint(1, 3),
             spoof=r.random() < .2, ueba=(6, 2), **kw)


def _a_customer_mule(a):  # the customer passes every check but forwards money for a fraud ring
    r = a.rng
    for k in range(r.randint(3, 8)):
        a.ev(a.at + k * r.uniform(2, 20) * 60, a.big(2, 10), a.mule(), authorized=True, ueba=(2, 6), flag=.3, **a.own())


def _a_repeat_scam(a):  # the same scammer is paid again and again, so that payee starts to look "known"
    r, p = a.rng, a.mule()
    amount, gap = a.big(1.5, 10), r.choice([7, 7, 14])
    for k in range(r.randint(4, 10)):
        a.ev(a.at + k * (gap + r.uniform(-1, 1)) * DAY, amount * r.uniform(.95, 1.05), p, authorized=True,
             ueba=(2.5, 5), flag=.1, **a.own())


def _a_bill_swap(a):  # a regular bill, on time and the usual amount, but paid into a new account
    rec = a.c.recurring[0] if a.c.recurring else None
    a.ev(a.at, rec.amount if rec else a.big(2, 8), a.mule(), ueba=(2.5, 5), flag=.3, **a.own())


def _a_amount_jump(a):  # the usual payee, but a far bigger amount
    c = a.c
    payee, base = (c.recurring[0].payee, c.recurring[0].amount) if c.recurring else (c.known(), c.median * 3)
    a.ev(a.at, base * a.rng.uniform(8, 50), payee, ueba=(3, 5), flag=.05, **a.own())


def _a_flagged(a):  # looks like the customer in every way, but the payee is on a mule list
    a.ev(a.at, a.big(.5, 3), a.mule(), ueba=(2, 6), flag=1.0, **a.own())


ATTACK_FN = {
    "account_takeover": _a_takeover, "sim_swap": _a_sim_swap, "scam_victim": _a_scam,
    "remote_access": _a_remote_access, "mule_fanout": _a_mule_fanout, "bot": _a_bot,
    "dormant_takeover": _a_dormant, "credential_stuffing": _a_stuffing, "card_testing": _a_card_testing,
    "test_then_drain": _a_test_drain, "slow_drip": _a_slow_drip, "bustout": _a_bustout,
    "new_account_fraud": _a_new_account, "beneficiary_abuse": _a_beneficiary, "limit_raise_drain": _a_limit,
    "device_binding_takeover": _a_binding, "profile_change_takeover": _a_profile,
    "impossible_travel": _a_impossible, "low_and_slow_mimic": _a_mimic, "stolen_device": _a_stolen,
    "customer_as_mule": _a_customer_mule, "flagged_payee": _a_flagged, "repeat_scam": _a_repeat_scam,
    "bill_payee_swap": _a_bill_swap, "recurring_amount_jump": _a_amount_jump,
}
ATTACKS = {"account_takeover": .12, "sim_swap": .09, "scam_victim": .12, "remote_access": .08,
           "mule_fanout": .06, "bot": .04, "dormant_takeover": .05, "credential_stuffing": .04,
           "card_testing": .04, "test_then_drain": .05, "slow_drip": .04, "bustout": .03,
           "new_account_fraud": .03, "beneficiary_abuse": .05, "limit_raise_drain": .03,
           "device_binding_takeover": .04, "profile_change_takeover": .03, "impossible_travel": .03,
           "low_and_slow_mimic": .05, "stolen_device": .04, "customer_as_mule": .03, "flagged_payee": .03,
           "repeat_scam": .04, "bill_payee_swap": .05, "recurring_amount_jump": .05}
assert set(ATTACKS) == set(ATTACK_FN)

SCENARIO_DOCS = {
    # genuine, from ordinary behaviour
    "routine": "usual payee, device, place, hour and amount",
    "travel_domestic": "on a trip to another Indian city",
    "travel_international": "abroad (or an NRI visiting India)",
    "new_phone": "first days on a new phone",
    "new_phone_travel": "new phone while travelling (the case that fooled the first model)",
    "new_payee": "paying someone for the first time",
    "new_network": "unfamiliar Wi-Fi or mobile network, same city",
    "large_purchase": "a big one-off payment, often to someone new",
    "bill_burst": "several bill payments within the hour",
    "after_sim_change": "a payment soon after a real SIM change",
    "after_password_reset": "a payment soon after a forgotten-password reset",
    "after_contact_change": "a payment soon after a real phone/email change",
    "failed_checks": "fumbled the face/voice check several times, then carried on",
    "false_spoof": "a genuine customer wrongly flagged as a spoof attempt",
    "odd_hour": "a payment far from the customer's usual hours",
    "high_behaviour_risk": "typing/behaviour unlike usual (tired, injured, new keyboard)",
    "flagged_payee_fp": "a genuine payee wrongly on a risk list",
    "dormant_return": "first payments after a long absence",
    "cold_start": "the first payments on a new account",
    "recurring": "rent, an EMI or a bill paid every month to the same payee",
    "recurring_quarterly": "a payment to the same payee every three months (maintenance, advance tax)",
    "recurring_half_yearly": "a payment to the same payee every six months (insurance premium, a bill)",
    "recurring_yearly": "a payment to the same payee once a year (annual rent, school fees, property tax)",
    "vpn_noise": "location jumps around because of a VPN",
    "assisted_payment": "a family member helps a senior from their own phone",
    "festival_spend": "heavier spending and new payees around a festival",
    "emergency_payment": "a large night-time payment to a new payee (hospital)",
    "genuine_add_beneficiary": "the customer adds a payee",
    "after_add_beneficiary": "paying a just-added payee, sometimes a big sweep to their own account",
    "limit_change": "the customer raises their limit",
    "after_limit_change": "a larger payment after a limit change",
    "profile_update": "the customer changes their contact details",
    "after_profile_update": "payments after a contact change",
    "genuine_device_binding": "the customer binds a new phone",
    "after_device_binding": "payments from a freshly bound phone",
    "split_payment": "one payment split in several because of a limit",
    "test_payment": "a rupee or two first, then the real payment, to a new payee",
    "payroll_batch": "a business pays 10-25 people at once",
    "cold_start_large": "a large first payment on a new account (deposit, fee)",
    "high_value_purchase": "a car, jewellery or fee paid to a new payee",
    "wedding_vendors": "many new vendors in two days",
    "retry_after_failure": "failed attempts, then the same payment succeeds",
    "micro_payment": "a UPI-size payment under Rs 100 (tea, auto, parking)",
    "very_large_payment": "Rs 10 lakh to 3 crore: property, insurance, tax, a business deal",
    "older_large_payment": "an older customer pays Rs 1 to 15 lakh: hospital, fixed deposit, family gift",
    "bustout_buildup": "small ordinary payments on an account that will later be drained (genuine-looking)",
    "impossible_travel_real_leg": "the customer's own payment just before a far-away attacker payment",
    # attacks
    "account_takeover": "attacker with stolen credentials on a new device, often after a reset",
    "sim_swap": "SIM taken over, OTP intercepted, large transfers",
    "scam_victim": "the real customer on their own phone, coached into paying a scammer",
    "remote_access": "scammer drives the customer's phone with a screen-sharing app",
    "mule_fanout": "a burst of payments to many new payees",
    "bot": "scripted payments over API or web",
    "dormant_takeover": "a long-dormant account is taken over after a password reset",
    "credential_stuffing": "known password, step-up checks keep failing",
    "card_testing": "many tiny card payments to new merchants",
    "test_then_drain": "a tiny test payment, then a large one to the same payee",
    "slow_drip": "payments just under the limit to one payee over days",
    "bustout": "an account built up with small payments, then drained",
    "new_account_fraud": "a brand-new account makes large payments at once",
    "beneficiary_abuse": "add a payee, then pay it moments later",
    "limit_raise_drain": "raise the limit, then drain the account",
    "device_binding_takeover": "bind the attacker's phone, then pay from it",
    "profile_change_takeover": "change the contact details, then pay",
    "impossible_travel": "a payment far from where the customer paid minutes earlier",
    "low_and_slow_mimic": "attacker copies the usual hour, size and city; only the device is new",
    "stolen_device": "the real phone in the wrong hands",
    "customer_as_mule": "the customer forwards money to many new payees for a fraud ring",
    "flagged_payee": "looks like the customer, but the payee is on a mule list",
    "repeat_scam": "the same scammer is paid again and again, so the payee starts to look familiar",
    "bill_payee_swap": "a regular bill at the usual amount, but paid into a new account",
    "recurring_amount_jump": "the usual payee, but eight to fifty times the usual amount",
}


def _degrade(s: Sim, feed: str) -> Sim:
    geo, events, ueba = FEED_HAS[feed]
    e, p = s.event, s.profile
    if not geo:
        e = replace(e, place=None, country=None)
    if not events:
        p = replace(p, sim_changed_at=None, contact_changed_at=None, password_reset_at=None, payee_risk_flag=False)
    if not ueba:
        p = replace(p, ueba_risk=None)
    return replace(s, event=e, profile=p)


def _life(c: Customer, rng: random.Random, days: int, attack_rate: float, attacks: Counter,
          scripts: Counter) -> list[Sim]:
    kind = None
    if rng.random() < attack_rate * AGE_GROUPS[c.age_group].risk:
        bias = AGE_ATTACK_BIAS.get(c.age_group, {})
        kind = _quota(attacks, {k: w * bias.get(k, 1) for k, w in ATTACKS.items()})
    at = max(START + rng.uniform(1, days - 1) * DAY, c.created + 14 * DAY)
    if kind in ("bustout", "new_account_fraud"):
        c.open_account(at, kind)
    elif at >= START + days * DAY:
        kind = None
    blackout = (at - c.rng.uniform(60, 120) * DAY, at) if kind == "dormant_takeover" else None
    if blackout:
        c.silence = None
    sims = [] if kind == "new_account_fraud" else c.genuine(blackout)
    if kind == "bustout":
        for s in sims:
            s.scenario = "bustout_buildup"
    for _ in range(rng.choices([0, 1, 2, 3], [.35, .35, .2, .1])[0]):
        name = _quota(scripts, {k: s.weight for k, s in SCRIPTS.items() if s.ok(c)})
        sims += [s for s in SCRIPTS[name].fn(c, START + rng.uniform(1, days - 1) * DAY)
                 if c.created <= s.event.at < START + days * DAY]
    if kind:
        sims += c.attack(kind, at)
    sims = [s for s in sims if s.event.at < START + days * DAY]
    return sorted((_degrade(s, c.feed) for s in sims), key=lambda s: s.event.at)


def simulate(n_customers: int, days: int, attack_rate: float, seed: int) -> Iterator[tuple[Customer, list[Sim]]]:
    rng = random.Random(seed)
    attacks, scripts = Counter(), Counter()
    for i in range(n_customers):
        c = Customer(f"c{seed}-{i}", random.Random(rng.random()), days)
        k = AGE_OVERSAMPLE.get(c.age_group, 1)
        people = [c]
        for j in range(k - 1):  # more people of the same age group
            while (x := Customer(f"c{seed}-{i}x{j}", random.Random(rng.random()), days)).age_group != c.age_group:
                pass
            people.append(x)
        for p in people:
            p.weight = 1 / k
            yield p, _life(p, rng, days, attack_rate, attacks, scripts)


def dataset(n_customers: int = 4000, days: int = 150, attack_rate: float = .15, seed: int = 7) -> list[Row]:
    """Feature rows for every fraud and a share of each genuine scenario (history is always full).
    Each row carries the weight that restores the scenario's real share of traffic."""
    rng = random.Random(seed + 1)
    rows: list[Row] = []
    for c, sims in simulate(n_customers, days, attack_rate, seed):
        lo = 0
        for i, s in enumerate(sims):
            e = s.event
            while sims[lo].event.at < e.at - HISTORY_DAYS * DAY:
                lo += 1
            share = 1.0 if s.label else SAMPLE.get(s.scenario, 1.0)
            if e.at >= START and rng.random() < share:
                history = [x.event for x in sims[lo:i]]
                rows.append(Row(build(e, history, s.profile), s.label, s.scenario, c.segment,
                                e.at, c.cid, c.arch, c.feed, c.weight / share, e.amount, c.age, c.age_group,
                                exposure_24h(e, history)))

    return rows
