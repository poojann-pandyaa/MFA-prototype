"""
The decision engine: given how risky a payment looks, pick the cheapest sequence of the user's authenticators.

Pure functions, no database, so it is easy to test. Method (see docs/authenticator-policy-plan.md):
  1. Turn the risk model's severity into a probability of scam: prior odds x likelihood ratio of that severity band.
  2. For every sequence of up to three available steps (plus "allow" and "block"), price
        friction of the steps taken + P(scam gets through) x amount + P(genuine user is stopped) x cost of stopping.
  3. Apply hard limits: user-time budget per severity, and slow steps only for rare, high-stakes cases.
  4. Return the cheapest plan with a plain reason and the remaining doubt after each step.

ASSUMPTIONS: the likelihood ratios come from the simulated evidence report (docs/evidence/results.json) and the
authenticator numbers from authn.registry. Neither is measured on real traffic.
"""
import itertools
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from authn.registry import FAMILIES, METHODS, Method

# How much more common each severity band is among scam payments than among genuine ones (simulation, 14 scam types).
SEVERITY_LR = {"MEDIUM": 5.7, "HIGH": 107.2, "CRITICAL": 6847.8}
DEFAULT_TIME_BUDGET = {"MEDIUM": 10.0, "HIGH": 30.0, "CRITICAL": None}
FAMILY_FOCUS = 0.8       # given a scam, how much of the probability sits on the model's named family
WHY = {
    "warning": "Scammers often ask for payment during a call or chat; a short warning breaks the script.",
    "typed_confirm": "Typing the registered name makes sure this is the person you mean to pay.",
    "payee_check": "Checks the registered name and what we know about this account.",
    "cooling_off": "A short wait gives time to reconsider and lets others notice.",
}


@dataclass
class PlanStep:
    index: int
    method: str
    name: str
    tier: str
    why: str
    est_seconds: float
    doubt_after: float          # probability this is a scam if the user passes this and every earlier step


@dataclass
class Plan:
    decision: str               # allow | steps | block
    p_scam: float               # before any step
    expected_cost: float
    steps: List[PlanStep] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    base_rate: float = 0.0
    time_seconds: float = 0.0   # user time on fast steps


def posterior(severity: str, prior: float) -> float:
    lr = SEVERITY_LR.get(severity)
    if lr is None:
        return 0.0
    odds = prior / (1 - prior) * lr
    return odds / (1 + odds)


def block_cost(amount: float) -> float:
    """ASSUMED cost of wrongly stopping a genuine payment: annoyance, abandonment, urgent cases."""
    return 100 + 0.005 * amount


def family_mix(family: str) -> Dict[str, float]:
    if family not in FAMILIES:
        return {f: 1 / len(FAMILIES) for f in FAMILIES}
    rest = (1 - FAMILY_FOCUS) / (len(FAMILIES) - 1)
    return {f: (FAMILY_FOCUS if f == family else rest) for f in FAMILIES}


def _run(steps: Sequence[Method], p: float, mix: Dict[str, float]):
    """Walk a sequence. Returns (friction cost per unit of traffic, P(scam completes), P(genuine completes), doubt after each step, stopped-genuine mass)."""
    reach_g, reach_f = 1 - p, {f: p * w for f, w in mix.items()}
    friction, stopped_g, doubts = 0.0, 0.0, []
    for m in steps:
        friction += (reach_g + sum(reach_f.values())) * m.cost
        stopped_g += reach_g * (1 - m.pass_genuine)
        reach_g *= m.pass_genuine
        reach_f = {f: v * m.pass_by_family.get(f, 1.0) for f, v in reach_f.items()}
        pf = sum(reach_f.values())
        doubts.append(pf / (pf + reach_g) if (pf + reach_g) > 0 else 0.0)
    return friction, sum(reach_f.values()), reach_g, doubts, stopped_g


def make_plan(severity: str, family: str, amount: float, enrolled: Iterable[str], prior: float,
              time_budget: Optional[Dict[str, Optional[float]]] = None, slow_amount: float = 50000.0, max_steps: int = 3, exclude: Iterable[str] = ()) -> Plan:
    if severity == "LOW":
        return Plan("allow", 0.0, 0.0, base_rate=prior, notes=["No unusual risk."])
    time_budget = DEFAULT_TIME_BUDGET if time_budget is None else time_budget
    p = posterior(severity, prior)
    mix = family_mix(family)
    enrolled = set(enrolled)
    exclude = set(exclude)   # steps that cannot work for this payment (e.g. typed confirmation when the payee has no registered name)
    pool: List[Method] = [m for m in METHODS.values() if m.implemented and (m.universal or m.id in enrolled) and m.id not in exclude]
    slow_ok = severity == "CRITICAL" or (severity == "HIGH" and family == "COACHED" and amount >= slow_amount)
    notes = []
    if not slow_ok:
        pool = [m for m in pool if m.tier != "slow"]
        notes.append("Slow steps are kept for the riskiest payments only.")
    budget = time_budget.get(severity)
    allow_cost = p * amount
    stop_cost = (1 - p) * block_cost(amount)
    best = (allow_cost, 0, 0.0, ())          # (cost, number of steps, user seconds, steps)
    candidates = []
    for n in range(1, max_steps + 1):
        for seq in itertools.permutations(pool, n):
            secs = sum(m.seconds for m in seq if m.tier == "fast")
            if budget is not None and secs > budget:
                continue
            friction, leak, genuine_ok, _, stopped_g = _run(seq, p, mix)
            cost = friction + stopped_g * block_cost(amount) + leak * amount
            candidates.append((cost, n, secs, seq))
    for c in candidates:
        if (c[0], c[1], c[2]) < (best[0], best[1], best[2]):
            best = c
    if stop_cost < best[0]:
        return Plan("block", p, stop_cost, notes=notes + ["Risk is too high for any available check to clear it."], base_rate=prior)
    seq = best[3]
    if not seq:
        return Plan("allow", p, allow_cost, notes=notes + ["Risk is low enough that extra friction costs more than it saves."], base_rate=prior)
    _, _, _, doubts, _ = _run(seq, p, mix)
    steps = [PlanStep(i, m.id, m.name, m.tier, WHY.get(m.id, ""), m.seconds, d) for i, (m, d) in enumerate(zip(seq, doubts))]
    return Plan("steps", p, best[0], steps, notes, prior, sum(m.seconds for m in seq if m.tier == "fast"))
