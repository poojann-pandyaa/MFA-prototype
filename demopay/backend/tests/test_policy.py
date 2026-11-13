"""Property tests for the decision engine: every combination must give a valid, bounded, explainable plan."""
import itertools

import pytest

from authn import policy
from authn.registry import FAMILIES, METHODS, UNIVERSAL_IMPLEMENTED

SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
AMOUNTS = [100, 5_000, 60_000, 300_000]
PRIORS = [1e-5, 1e-3]
ENROLLED = [set(UNIVERSAL_IMPLEMENTED), set()]


def combos():
    return itertools.product(SEVERITIES, FAMILIES, AMOUNTS, PRIORS, ENROLLED, [(), ("typed_confirm",)])


def test_every_combination_gives_a_valid_plan():
    for sev, fam, amt, prior, enrolled, excl in combos():
        pl = policy.make_plan(sev, fam, amt, enrolled, prior, exclude=excl)
        assert pl.decision in ("allow", "steps", "block")
        ids = [s.method for s in pl.steps]
        assert len(ids) == len(set(ids)) <= 3
        assert (pl.decision == "steps") == bool(ids)
        for i in ids:
            m = METHODS[i]
            assert m.implemented and (m.universal or i in enrolled) and i not in excl
        if sev == "LOW":
            assert pl.decision == "allow"


def test_time_budget_and_slow_gate_are_obeyed():
    for sev, fam, amt, prior, enrolled, excl in combos():
        pl = policy.make_plan(sev, fam, amt, enrolled, prior, exclude=excl)
        fast = sum(s.est_seconds for s in pl.steps if s.tier == "fast")
        budget = policy.DEFAULT_TIME_BUDGET.get(sev)
        if budget is not None:
            assert fast <= budget + 1e-9
        slow_ok = sev == "CRITICAL" or (sev == "HIGH" and fam == "COACHED" and amt >= 50000)
        if not slow_ok:
            assert all(s.tier != "slow" for s in pl.steps)


def test_chosen_plan_is_never_dearer_than_allow_or_block():
    for sev, fam, amt, prior, enrolled, excl in combos():
        if sev == "LOW":
            continue
        pl = policy.make_plan(sev, fam, amt, enrolled, prior, exclude=excl)
        p = policy.posterior(sev, prior)
        assert pl.expected_cost <= p * amt + 1e-9
        assert pl.expected_cost <= (1 - p) * policy.block_cost(amt) + 1e-9


def test_passing_a_step_never_raises_the_doubt():
    for sev, fam, amt, prior, enrolled, excl in combos():
        pl = policy.make_plan(sev, fam, amt, enrolled, prior, exclude=excl)
        last = pl.p_scam
        for s in pl.steps:
            assert s.doubt_after <= last + 1e-12
            last = s.doubt_after


def test_more_available_methods_never_cost_more():
    for sev, fam, amt, prior in itertools.product(["MEDIUM", "HIGH", "CRITICAL"], FAMILIES, AMOUNTS, PRIORS):
        with_all = policy.make_plan(sev, fam, amt, UNIVERSAL_IMPLEMENTED, prior).expected_cost
        fewer = policy.make_plan(sev, fam, amt, UNIVERSAL_IMPLEMENTED, prior, exclude=("typed_confirm", "payee_check")).expected_cost
        assert with_all <= fewer + 1e-9


def test_higher_amount_is_never_planned_lighter():
    """More money at stake: the plan must not have a smaller chance of letting a scam through."""
    def leak(pl):
        return pl.steps[-1].doubt_after if pl.steps else (pl.p_scam if pl.decision == "allow" else 0.0)
    for sev, fam, prior in itertools.product(["HIGH", "CRITICAL"], FAMILIES, PRIORS):
        small = leak(policy.make_plan(sev, fam, 5_000, UNIVERSAL_IMPLEMENTED, prior))
        big = leak(policy.make_plan(sev, fam, 300_000, UNIVERSAL_IMPLEMENTED, prior))
        assert big <= small + 1e-9


def test_posterior_grows_with_severity_and_base_rate():
    assert policy.posterior("MEDIUM", 1e-3) < policy.posterior("HIGH", 1e-3) < policy.posterior("CRITICAL", 1e-3)
    assert policy.posterior("HIGH", 1e-5) < policy.posterior("HIGH", 1e-3)
    assert policy.posterior("LOW", 1e-3) == 0.0


@pytest.mark.parametrize("sev,fam", [("MEDIUM", "COACHED"), ("HIGH", "UNAUTHORISED")])
def test_slow_wait_is_not_used_for_ordinary_risk(sev, fam):
    pl = policy.make_plan(sev, fam, 300_000, UNIVERSAL_IMPLEMENTED, 1e-3)
    assert "cooling_off" not in [s.method for s in pl.steps]


def test_very_high_doubt_with_big_amount_blocks():
    pl = policy.make_plan("CRITICAL", "COACHED", 300_000, UNIVERSAL_IMPLEMENTED, 1e-2)
    assert pl.decision in ("block", "steps")
    pl = policy.make_plan("CRITICAL", "UNAUTHORISED", 2_000_000, UNIVERSAL_IMPLEMENTED, 1e-1)
    assert pl.decision == "block"
