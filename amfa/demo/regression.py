"""Regression matrix: every demo customer in every situation, through the real decision path.

    python -m amfa.demo.regression                 # print the matrix, exit 1 if an expectation fails
    python -m amfa.demo.regression --markdown docs/regression-report.md

Each situation says what a careful bank should do. Two kinds of check:
  * invariants that must always hold, whatever the model learned (a customer paying as usual is never asked for more
    than a code and a typing check; the situations that look like fraud always cost at least as much as the same
    customer paying as usual, and always involve a strong check (face or voice) or a person);
  * the friction budget: how much a customer is asked for, in "seconds of effort" (see evaluate.FRICTION).
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from amfa.core import db, factors
from amfa.demo import personas as P
from amfa.demo.evaluate import LEVELS, Result, evaluate
from amfa.engine.context import Factor
from amfa.engine.policy import TenantPolicy

STRONG = {"FACE", "VOICE"}
# the most a customer paying as usual may be asked for: a one-time code plus the quiet typing check
ROUTINE_BUDGET = 1.5


BIG = 30_000.0      # above the bank's MEDIUM line (Rs 10,000): the money a thief goes for, whatever the customer usually pays


def scenarios_for(p: P.Persona) -> list[P.Scenario]:
    m = max(p.median, 100.0)
    out = [
        P.Scenario(p.username, "routine", round(m, -1) or 250, note="the customer pays as they always do"),
        P.Scenario(p.username, "tiny to usual payee", 50, note="small, to someone they pay often"),
        P.Scenario(p.username, "bigger than usual", m * 6, note="six times a normal payment, same payee, same phone"),
        P.Scenario(p.username, "new payee, modest", m, payee="new", note="a normal amount to someone new"),
        P.Scenario(p.username, "new device", m, device="new", note="the usual payment from a phone never seen before"),
        P.Scenario(p.username, "other network", m, network="other", note="from a network the customer has not used"),
        P.Scenario(p.username, "new payee, large, 3 am", max(m * 12, BIG), payee="new", hour=3, fraud=True,
                   note="a large first payment in the middle of the night"),
        P.Scenario(p.username, "account takeover", max(m * 30, BIG), payee="new", device="new", hour=2, network="other", fraud=True,
                   events={"simChangedAt": 1200, "contactChangedAt": 900}, note="SIM swapped 20 minutes ago, new phone, new payee, large"),
        P.Scenario(p.username, "contact changed, then a big payment", max(m * 15, BIG), payee="new", fraud=True,
                   events={"contactChangedAt": 3600}, note="the email was changed an hour ago, then a large payment to someone new"),
        P.Scenario(p.username, "small takeover payment", m, payee="new", device="new", hour=2, network="other", fraud=True,
                   events={"simChangedAt": 1200}, note="SIM swapped and a new phone, but a small amount: kept light by design (the small-payment cap)"),
    ]
    extra = {
        "meera": [P.Scenario("meera", "rent as usual", 25_000, payee="BEN-MEERA1", note="the monthly rent, to the same landlord")],
        "suresh": [P.Scenario("suresh", "coached scam payment", 150_000, payee="new", hour=11, fraud=True,
                              note="a senior, on the phone with a 'bank officer', sends a lakh and a half to a first-time payee by day on their own phone")],
        "priya": [P.Scenario("priya", "routine bulk order", 20_000, payee="BEN-PRIYA1", note="a normal large order to the main supplier")],
        "vikram": [P.Scenario("vikram", "routine large transfer", 150_000, payee="BEN-VIKRAM1", note="the usual family transfer")],
        "dev": [P.Scenario("dev", "first payment, small", 500, payee="new", note="a new customer's first payment"),
                P.Scenario("dev", "first payment, large", 50_000, payee="new", fraud=True, note="a new account moving a lot on day one")],
    }
    return out + extra.get(p.username, [])


@dataclass
class Row:
    persona: str
    scenario: P.Scenario
    result: Result
    factors: int = 1          # factors the customer has enrolled in this run (a code, typing, face, voice)
    strong: bool = False      # ...and whether one of them is a face or a voice


def _routine(rows: list[Row], persona: str) -> Optional[Row]:
    return next((r for r in rows if r.persona == persona and r.scenario.name == "routine"), None)


ROUTINE = {"routine", "tiny to usual payee", "rent as usual", "routine bulk order", "routine large transfer"}


def enrolled_factors(p: P.Persona, biometrics: bool = True) -> int:
    """How many of the two-factor floor's factors the customer has: a code, typing, and (when seeded) a face and a voice."""
    return 1 + int(p.typing) + (int(p.face) + int(p.voice) if biometrics else 0)


def has_strong(p: P.Persona, biometrics: bool = True) -> bool:
    return biometrics and (p.face or p.voice)


def problems(rows: list[Row]) -> list[str]:
    """Everything that breaks an expectation, in words. (Design trade-offs are listed by findings(), not here.)"""
    out = []
    for r in rows:
        sc, res, p = r.scenario, r.result, P.persona(r.persona)
        n, strong = r.factors, r.strong
        who = f"{r.persona} / {sc.name}"
        # 1. a customer who meets the two-factor floor and pays as usual is not made to work for it
        if sc.name in ROUTINE and n >= 2:
            budget = ROUTINE_BUDGET + (2.0 if p.segment == "PREMIUM" else 0.0)
            if res.friction > budget:
                out.append(f"{who}: paying as usual costs {res.checks}{' + review' if res.review else ''} (friction {res.friction}, budget {budget})")
        # 2. a customer below the floor is never approved with fewer checks than the rule demands: a person looks
        if n < 2 and not res.review:
            out.append(f"{who}: only {n} factor enrolled, yet approved with {res.checks} and no review")
        if sc.fraud:
            base = _routine(rows, r.persona)
            # 3. what looks like fraud never costs less than the same customer paying as usual
            if base and res.friction < base.result.friction:
                out.append(f"{who}: costs less ({res.friction}) than paying as usual ({base.result.friction})")
            # 4. a strong check the customer has is used when the money is large (at or above the bank's MEDIUM line)
            if sc.amount >= 10_000 and strong and not (set(res.checks) & STRONG or res.review):
                out.append(f"{who}: a large payment that looks like fraud, and a customer with a face or voice check, got {res.checks}")
            # 5. a recent SIM, password or contact change followed by a large payment to a new payee is held for a person
            if sc.events and sc.payee == "new" and sc.amount >= 10_000 and not res.review:
                out.append(f"{who}: an account change then a large payment to a new payee was not held for a person")
        # 6. the model rises with the danger
    for p in P.PERSONAS:
        a, b = _routine(rows, p.username), next((x for x in rows if x.persona == p.username and x.scenario.name == "account takeover"), None)
        if a and b and a.result.plan.riskScore is not None and b.result.plan.riskScore is not None \
                and b.result.plan.riskScore <= a.result.plan.riskScore:
            out.append(f"{p.username}: the risk score for a takeover ({b.result.plan.riskScore}) is not above routine ({a.result.plan.riskScore})")
    return out


def findings(rows: list[Row]) -> list[str]:
    """Behaviour that is by design but a bank should know about, so a demo and a review can talk about it."""
    out = []
    below = {r.persona: P.persona(r.persona) for r in rows if r.factors < 2}
    for p in below.values():
        out.append(f"{p.name} has only a one-time code enrolled, which cannot meet the two-factor minimum, so every payment, "
                   f"even a routine one, goes to a person. A second factor at sign-up (the quiet typing check) removes that.")
    weak = [r for r in rows if r.scenario.fraud and (r.result.level or "") in ("T3", "T4") and not (set(r.result.checks) & STRONG) and not r.result.review]
    if weak:
        who = sorted({f"{r.persona} / {r.scenario.name}" for r in weak})
        out.append("The model rated these T3 or T4 but the customer has no face or voice check, so the plan is only a code and typing "
                   "(the low-touch rule uses the strongest checks available instead of a person): " + "; ".join(who))
    small = [r for r in rows if r.scenario.name == "small takeover payment" and not r.result.review]
    if small:
        out.append("A SIM swap with a new phone but a small amount stays at a code and typing by design (small-payment cap, "
                   "hard holds need an amount at or above the bank's MEDIUM line): " + ", ".join(sorted({r.persona for r in small})))
    return out


def run(s, tenant: db.Tenant, *, now: Optional[float] = None, home_ip: str = "127.0.0.1") -> list[Row]:
    now = now or time.time()
    for p in P.PERSONAS:
        P.seed_amfa(s, tenant, p, now=now, home_ips=(home_ip,), biometrics=True)
    s.flush()
    return [Row(p.username, sc, evaluate(s, tenant, p, sc, now=now, home_ip=home_ip), enrolled_factors(p), has_strong(p))
            for p in P.PERSONAS for sc in scenarios_for(p)]


def run_seeded(s, tenant: db.Tenant, *, now: Optional[float] = None, home_ip: str = "127.0.0.1") -> list[Row]:
    """The customers `scripts/seed_demo.py` put in the demo, with what the demo really gave them (no face or voice)."""
    now = now or time.time()
    return [Row(p.username, sc, evaluate(s, tenant, p, sc, now=now, home_ip=home_ip), enrolled_factors(p, False), False)
            for p in P.PERSONAS for sc in scenarios_for(p)]


def stub_behaviour(monkeypatch_or_none=None) -> Callable:
    """A stand-in for the behaviour service: calm for a payee and phone the customer already uses, unsure otherwise."""
    def fake(path, body):
        if path != "/risk/preview":
            return None
        score = 0.15 if body.get("deviceKey") in _known["devices"] and body.get("payeeKey") in _known["payees"] else 0.55
        band = "LOW" if score < 0.3 else "MEDIUM"
        return {"riskScore": score, "band": band, "status": "OK", "dimensionsUsed": ["transaction"], "latencyMs": 1}
    return fake


_known: dict = {"devices": set(), "payees": set()}


def learn_known(s, tenant) -> None:
    from sqlalchemy import select
    from amfa.core.context_builder import device_key, payee_key
    _known["devices"] = {device_key(tenant.id, d) for p in P.PERSONAS for d in p.devices}
    _known["payees"] = {payee_key(tenant.id, x.ref) for p in P.PERSONAS for x in p.payees}


def markdown(rows: list[Row], title: str = "Regression report") -> str:
    lines = [f"# {title}", "", f"Run {time.strftime('%Y-%m-%d %H:%M')}. Friction counts a code as 1, typing as 0.5, face or voice as 2, a person's review as 4.", ""]
    for p in P.PERSONAS:
        lines += [f"## {p.name} ({p.username})", f"*{p.story}*", "",
                  "| Situation | Threat | Risk | Checks asked | Friction | Fraud-like |", "|---|---|---|---|---|---|"]
        for r in (x for x in rows if x.persona == p.username):
            res = r.result
            score = "" if res.plan.riskScore is None else f"{res.plan.riskScore:.2f}"
            checks = ", ".join(res.checks) + (" + review" if res.review else "")
            lines.append(f"| {r.scenario.name} | {res.level or res.plan.riskBand.value} | {score} | {checks or 'none'} | {res.friction:g} | {'yes' if r.scenario.fraud else ''} |")
        lines.append("")
    bad = problems(rows)
    lines += ["## Expectations", "", "All expectations hold." if not bad else "\n".join(f"- FAILED: {b}" for b in bad), ""]
    lines += ["## Findings (by design, worth knowing)", ""] + [f"- {f}" for f in findings(rows)] + [""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", help="write the report here")
    ap.add_argument("--live-behaviour", action="store_true", help="use the running behaviour (UEBA) service instead of the stand-in")
    ap.add_argument("--seeded", metavar="BANK_SLUG", help="evaluate the customers already seeded for this bank (implies --live-behaviour); nothing is written")
    ap.add_argument("--home-ip", default="127.0.0.1", help="the address the seeded customers bank from")
    a = ap.parse_args(argv)
    db.migrate()
    with db.session() as s:
        if a.seeded:
            from sqlalchemy import select
            tenant = s.execute(select(db.Tenant).where(db.Tenant.slug == a.seeded)).scalars().one()
            rows = run_seeded(s, tenant, home_ip=a.home_ip)
        else:
            tenant = db.Tenant(id=f"tn_reg_{uuid.uuid4().hex[:8]}", name="Regression Bank", slug=f"reg{uuid.uuid4().hex[:8]}",
                               policy=TenantPolicy().model_dump(mode="json"))
            s.add(tenant)
            s.flush()
            if not a.live_behaviour:
                learn_known(s, tenant)
                factors.ueba = stub_behaviour()
            rows = run(s, tenant, home_ip=a.home_ip)
        report = markdown(rows, "Regression report" + (" (the seeded demo customers, live behaviour service)" if a.seeded else ""))
        s.rollback()
    print(report)
    if a.markdown:
        open(a.markdown, "w").write(report)
    return 1 if problems(rows) else 0


if __name__ == "__main__":
    sys.exit(main())
