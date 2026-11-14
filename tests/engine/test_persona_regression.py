"""Regression: every demo customer in every situation, through the real decision path, with a trained risk model.

The behaviour service is replaced by a stand-in (calm for a payee and phone the customer already uses, unsure
otherwise); the live service is exercised by `python -m amfa.demo.regression --live-behaviour`."""
import uuid

import pytest

from amfa.core import db, factors
from amfa.demo import personas as P
from amfa.demo import regression as R
from amfa.engine.policy import TenantPolicy
from amfa.engine.risk import scorer, train


@pytest.fixture(scope="module")
def rows():
    train.run(customers=3000, search=[train.SEARCH[0]], seeds=1, force=True)
    scorer._load.cache_clear()
    db.migrate()
    with db.session() as s:
        tenant = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="Regression Bank", slug=f"r{uuid.uuid4().hex[:8]}",
                           policy=TenantPolicy().model_dump(mode="json"))
        s.add(tenant)
        s.flush()
        R.learn_known(s, tenant)
        original = factors.ueba
        factors.ueba = R.stub_behaviour()
        try:
            yield R.run(s, tenant)
        finally:
            factors.ueba = original
            s.rollback()


def get(rows, persona, name):
    return next(r for r in rows if r.persona == persona and r.scenario.name == name)


def test_every_customer_has_every_situation(rows):
    assert {r.persona for r in rows} == {p.username for p in P.PERSONAS}
    assert len(rows) == sum(len(R.scenarios_for(p)) for p in P.PERSONAS)


def test_the_model_was_used_not_the_rules_fallback(rows):
    assert all(r.result.plan.modelError is None and r.result.plan.riskScore is not None for r in rows)


def test_nothing_that_should_be_easy_or_hard_is_the_wrong_way_round(rows):
    assert R.problems(rows) == []


def test_a_known_customer_is_asked_for_less_than_the_same_customer_in_a_takeover(rows):
    for p in P.PERSONAS:
        if p.username == "dev":
            continue
        takeover, routine = get(rows, p.username, "account takeover").result.friction, get(rows, p.username, "routine").result.friction
        # a customer already sent to a person for everything cannot be asked for more than that
        assert (takeover >= routine) if R.enrolled_factors(p) < 2 else (takeover > routine), p.username


def test_the_risk_score_rises_from_routine_to_takeover(rows):
    for p in P.PERSONAS:
        if p.username == "dev":
            continue
        routine = get(rows, p.username, "routine").result.plan.riskScore
        takeover = get(rows, p.username, "account takeover").result.plan.riskScore
        assert takeover > routine, (p.username, routine, takeover)


def test_a_senior_coached_into_a_big_first_payment_is_stopped_or_challenged_hard(rows):
    r = get(rows, "suresh", "coached scam payment").result
    assert (set(r.checks) & R.STRONG) or r.review
    assert r.plan.scamRisk                      # and the customer gets the scam warning


def test_the_regular_rent_does_not_cost_more_than_any_other_routine_payment(rows):
    rent = get(rows, "meera", "rent as usual").result
    assert rent.friction <= get(rows, "meera", "routine").result.friction + 1.0
