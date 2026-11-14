"""Run the real decision path (the same assemble + decide the API uses) for a persona in a scenario, without a session."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from amfa.core import context_builder, db
from amfa.core.context_builder import TxnInput
from amfa.demo.personas import DAY, TZ, Persona, Scenario
from amfa.engine.context import Factor, Plan, TxnType
from amfa.engine.decide import decide

# what a check costs the customer: a code is a few seconds, a typing check is nearly invisible, a face or voice
# check is a deliberate step, and a person looking at it is a wait
FRICTION = {Factor.OTP: 1.0, Factor.UEBA: 0.5, Factor.FACE: 2.0, Factor.VOICE: 2.0}
REVIEW_FRICTION = 4.0
LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


@dataclass
class Result:
    plan: Plan
    friction: float
    checks: list[str]
    level: Optional[str]
    review: bool


def friction_of(plan: Plan) -> float:
    return sum(FRICTION[f] for f in plan.sequence()) + (REVIEW_FRICTION if plan.escalate else 0.0)


def evaluate(s, tenant: db.Tenant, p: Persona, sc: Scenario, *, now: Optional[float] = None, home_ip: str = "127.0.0.1",
             other_ip: str = "198.18.99.9", customer_id: Optional[str] = None) -> Result:
    now = now or time.time()
    cid = customer_id or f"cu_demo_{p.username}"
    customer = s.get(db.Customer, cid)
    usual = max(p.payees, key=lambda x: x.weight).ref if p.payees else None
    ref = usual if sc.payee == "usual" else sc.payee
    created = None
    if sc.payee == "new":
        ref, created = f"BEN-NEW-{sc.name[:8].upper().replace(' ', '')}", now
    else:
        known = next((x for x in p.payees if x.ref == ref), None)
        created = (now - known.age_days * DAY) if known else None
    at = now
    if sc.hour is not None:
        at = datetime.fromtimestamp(now, TZ).replace(hour=sc.hour, minute=30, second=0).timestamp()
    events = {k: at - v for k, v in sc.events.items() if not isinstance(v, bool)} | {k: v for k, v in sc.events.items() if isinstance(v, bool)}
    t = TxnInput(type=TxnType(sc.kind), amount_minor=int(sc.amount * 100), currency="INR", payee_ref=ref, channel="MOBILE",
                 device_id=p.devices[0] if sc.device == "usual" else "android_unknown_device",
                 ip=home_ip if sc.network == "home" else other_ip, at=at, payee_created_at=created,
                 capability={"camera": True, "microphone": True, "touch": True, "pointer": False}, account_events=events or None)
    a = context_builder.assemble(s, tenant, customer, "txn_eval", t)
    plan = decide(a.ctx, agent=None, features=a.features)
    return Result(plan, friction_of(plan), [f.value for f in plan.sequence()], plan.threatLevel, bool(plan.escalate))
