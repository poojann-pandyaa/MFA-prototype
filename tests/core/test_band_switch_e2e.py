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
    """With the trained model, whether a band qualifies at all is close (the ₹10k-1 lakh band only just does, near day 115) and the
    training is not bit-for-bit reproducible, so this only checks that any band that moved did so by the rules. That a band does
    move, on schedule, is checked deterministically with the perfect model below."""
    assert len(reported.timeline) == reported.days == 150
    for b, st in reported.bands.items():
        if st.model_day is None:
            continue
        assert st.eligible_day is not None and st.model_day >= st.eligible_day >= EARLIEST_DAY
        before = [states[b] for day, states in reported.timeline if day < st.eligible_day]
        assert set(before) <= {"COLLECTING"}


@pytest.fixture(scope="module")
def perfect(simulated_bank):
    """The same bank with a stand-in model that is never wrong: a biometric for every fraud, nothing extra for anyone honest. It
    beats the rules wherever there is enough evidence, so when its bands move is fixed by the gates alone."""
    fraud = {id(r.features) for r in simulated_bank if r.label}

    class Perfect:
        version = "perfect-v1"

        def score(self, features):
            bad = id(features) in fraud
            return scorer.Result(score=0.9 if bad else 0.01, raw=0.9 if bad else 0.01, level="T3" if bad else "T0", reasons=[],
                                 version=self.version)

    mp = pytest.MonkeyPatch()
    mp.setattr(scorer, "active", lambda: Perfect())
    try:
        yield band_replay.replay(simulated_bank, policy=POLICY, label_source="chargeback", compare_off=False)
    finally:
        mp.undo()


def test_a_model_that_beats_the_rules_earns_its_bands_on_schedule_and_keeps_them(perfect):
    switched = {b: st for b, st in perfect.bands.items() if st.model_day is not None}
    assert 2 in switched, "the ₹10k-1 lakh band has the evidence and a model that beats the rules: it must move"
    for b, st in switched.items():
        assert st.eligible_day >= EARLIEST_DAY and st.model_day == st.eligible_day     # approved the night it qualified
        states = [s[b] for _, s in perfect.timeline]
        assert set(states[:st.eligible_day]) == {"COLLECTING"}
        assert set(states[st.model_day:]) == {"MODEL"} and st.fallback_days == []    # a model that never loses keeps it


def test_a_band_that_did_not_move_was_short_of_evidence_or_of_weeks_in_a_row_even_for_a_perfect_model(perfect):
    for b, st in perfect.bands.items():
        if st.model_day is None:
            short = any(m.startswith("needs") for m in st.last_missing)
            still_building = not st.last_missing and st.last_streak < MIN.passingEvaluations
            assert short or still_building, (b, st.last_missing, st.last_streak)


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


# ---- #7: a model update partway through the bank's life ------------------------------------------------------------------------

def _perfect(version, fraud):
    class Perfect:
        def __init__(self):
            self.version = version

        def score(self, features):
            bad = id(features) in fraud
            return scorer.Result(score=0.9 if bad else 0.01, raw=0.9 if bad else 0.01, level="T3" if bad else "T0", reasons=[],
                                 version=version)
    return Perfect()


class Store:
    """The model store as the replay sees it: who decides, who watches, and promotion switching the two."""
    def __init__(self, mp, active):
        self.active, self.candidate = active, None
        mp.setattr(scorer, "active", lambda: self.active)
        mp.setattr(scorer, "candidate", lambda: self.candidate)
        mp.setattr(scorer, "candidate_version", lambda store=None: self.candidate.version if self.candidate else None)

        def promote(version, store=None):
            assert self.candidate is not None and self.candidate.version == version
            self.active, self.candidate = self.candidate, None
        mp.setattr(scorer, "promote_candidate", promote)


def _only_this_bank_switched_on(tenant_id):
    with db.session() as s:
        for t in s.execute(db.select(db.Tenant)).scalars().all():
            p = TenantPolicy.model_validate(t.policy)
            if t.id != tenant_id and p.collection.enabled:
                d = dict(t.policy)
                d["collection"] = dict(d["collection"]) | {"enabled": False}
                t.policy = d


def _replay_with_update(simulated_bank, update):
    fraud = {id(r.features) for r in simulated_bank if r.label}
    mp = pytest.MonkeyPatch()
    store = Store(mp, _perfect("perfect-v1", fraud))
    seen = {"tenant": None, "promoted_on": None}

    def on_day(day, rep, tenant_id, day_start):
        seen["tenant"] = tenant_id
        update(day, store, fraud, seen, tenant_id)

    try:
        rep = band_replay.replay(simulated_bank, policy=POLICY, label_source="chargeback", compare_off=False, on_day=on_day)
    finally:
        mp.undo()
    with db.session() as s:
        final = {b: (st.state, st.model_version) for b in range(4) if (st := s.get(db.BandGateState, (seen["tenant"], b)))}
    return rep, seen, final


@pytest.fixture(scope="module")
def shadowed_update(simulated_bank):
    """V1 decides from day one. V2 is installed as the candidate on day 5 and watches. The night after V2 has been trusted in the
    ₹10k-1 lakh band while V1 is deciding it, V2 is promoted (promotion refuses anything that would drop a band)."""
    def update(day, store, fraud, seen, tenant_id):
        from amfa.core import band_gate
        if day == 5:
            store.candidate = _perfect("perfect-v2", fraud)
        if seen["promoted_on"] is None and store.candidate is not None:
            with db.session() as s:
                bc, st = s.get(db.BandCandidate, (tenant_id, 2)), s.get(db.BandGateState, (tenant_id, 2))
                ready = bc is not None and bc.state == band_gate.APPROVED and st is not None and st.state == band_gate.MODEL
            if ready:
                _only_this_bank_switched_on(tenant_id)
                band_gate.promote("sim_platform_operator")
                seen["promoted_on"] = day
    return _replay_with_update(simulated_bank, update)


@pytest.fixture(scope="module")
def swapped_weights(simulated_bank):
    """The same bank, but the day after V1 has been put on the ₹10k-1 lakh band, new weights are loaded directly as the active
    model, with no candidate."""
    def update(day, store, fraud, seen, tenant_id):
        if seen.get("swapped_on") is None:
            with db.session() as s:
                st = s.get(db.BandGateState, (tenant_id, 2))
                on_model = st is not None and st.state == "MODEL"
            if on_model:
                store.active = _perfect("perfect-v2", fraud)
                seen["swapped_on"] = day
    return _replay_with_update(simulated_bank, update)


def test_a_shadowed_update_keeps_the_band_on_the_model_with_no_gap(shadowed_update):
    rep, seen, final = shadowed_update
    st = rep.bands[2]
    assert seen["promoted_on"] is not None, "V2 was never trusted in the band while V1 decided it"
    assert st.model_day is not None and st.model_day < seen["promoted_on"]          # V1 earned it first
    assert st.candidate_approved_day is not None and 5 + EARLIEST_DAY <= st.candidate_approved_day <= seen["promoted_on"]
    states = [s[2] for _, s in rep.timeline]
    assert set(states[st.model_day:]) == {"MODEL"}, "the band spent a day on the rules because of the model update"
    assert final[2] == ("MODEL", "perfect-v2")


def test_bands_the_candidate_never_earned_end_up_on_the_new_version_collecting(shadowed_update):
    rep, seen, final = shadowed_update
    for b, (state, version) in final.items():
        assert version == "perfect-v2"
        if rep.bands[b].candidate_approved_day is None:
            assert state != "MODEL"


def test_weights_swapped_in_directly_are_never_trusted_and_the_band_drops_the_same_day(swapped_weights):
    rep, seen, final = swapped_weights
    day = seen.get("swapped_on")
    assert day is not None, "V1 never earned the band, so there was nothing to swap under"
    states = [s[2] for _, s in rep.timeline]
    assert states[day - 1] == "MODEL" and states[day] == "COLLECTING"
    # V2 has no answers of its own from before the swap, and needs months to earn the band: never within this replay
    assert set(states[day:]) == {"COLLECTING"} and final[2] == ("COLLECTING", "perfect-v2")
