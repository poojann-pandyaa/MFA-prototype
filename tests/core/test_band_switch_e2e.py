"""Phase 5 of the band switch: a simulated bank replayed from its first day through the real decision code, grading and approvals
(amfa/demo/band_replay.py). Uses a small trained model and the hard-minimum gates, so a small bank can reach them in 150 days."""
import pytest

from amfa.core import db
from amfa.demo import band_replay
from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy
from amfa.engine.risk import scenarios as S
from amfa.engine.risk import scorer, train
from amfa.engine.risk.collection_bands import TOP

MIN = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4)
POLICY = TenantPolicy(collection=CollectionPolicy(enabled=True, gate=MIN, topGate=MIN))
EARLIEST_DAY = 30 + 7 + 7 * 3          # 30 days to mature, a week to close, then four weekly passes (the first counts on day one)


@pytest.fixture(scope="module")
def simulated_bank():
    train.run(customers=3000, search=[train.SEARCH[0]], seeds=1, force=True)
    scorer._load.cache_clear()
    db.migrate()
    return S.dataset(n_customers=700, days=150, attack_rate=0.35, seed=11)


@pytest.fixture(scope="module")
def reported(simulated_bank):
    return band_replay.replay(simulated_bank, policy=POLICY, label_source="chargeback")


@pytest.fixture(scope="module")
def unreported(simulated_bank):
    """The same bank with no fraud ever reported by the bank (labels from a source the grading never accepts). A quarter of its
    customers is enough: without accepted labels no band can qualify, however many payments there are."""
    some = set(sorted({r.customer for r in simulated_bank})[::4])
    return band_replay.replay([r for r in simulated_bank if r.customer in some], policy=POLICY, label_source="simulator",
                              compare_off=False)


def test_the_switch_never_asked_for_less_than_the_switch_off_on_any_payment(reported):
    assert reported.payments > 10_000
    assert reported.weaker_than_off == 0


def test_a_band_moves_to_the_model_only_after_its_gates_pass(reported):
    switched = {b: st for b, st in reported.bands.items() if st.model_day is not None}
    assert switched, "no band earned the model in 150 days of this bank"
    for b, st in switched.items():
        assert st.eligible_day is not None and st.model_day >= st.eligible_day >= EARLIEST_DAY
        before = [states[b] for day, states in reported.timeline if day < st.eligible_day]
        assert set(before) <= {"COLLECTING"}


def test_while_collecting_fraud_caught_is_at_least_what_the_model_alone_would_catch(reported):
    for b, st in reported.bands.items():
        modes = ("COLLECTING", "ELIGIBLE")
        caught, alone = sum(st.fraud_caught[m] for m in modes), sum(st.model_alone_fraud_caught[m] for m in modes)
        assert caught >= alone, (b, caught, alone)


def test_with_no_real_fraud_reports_no_band_ever_leaves_the_rules(unreported):
    assert all(st.eligible_day is None and st.model_day is None for st in unreported.bands.values())
    assert all(set(states.values()) == {"COLLECTING"} for _, states in unreported.timeline)


def test_the_lakh_band_stays_on_the_rules_without_real_fraud_reports(unreported):
    assert unreported.bands[TOP].model_day is None
