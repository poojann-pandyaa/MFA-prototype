"""The simulated dataset: every scenario is present, labelled sensibly, and the edge cases really occur."""
import csv
import math

import pytest

from amfa.engine.risk import export
from amfa.engine.risk.features import FEATURES
from amfa.engine.risk.scenarios import (AGE_GROUPS, AMOUNT_BANDS, ARCHETYPES, ATTACKS, FEEDS, SCENARIO_DOCS,
                                          dataset)


@pytest.fixture(scope="module")
def rows():
    return dataset(n_customers=1500, seed=3)


def test_every_documented_scenario_has_rows(rows):
    seen = {r.scenario for r in rows}
    assert not [t for t in SCENARIO_DOCS if t not in seen], "scenarios with no rows"
    assert not [t for t in seen if t not in SCENARIO_DOCS], "undocumented scenarios"
    assert {r.archetype for r in rows} == set(ARCHETYPES) and {r.feed for r in rows} == set(FEEDS)
    assert {r.segment for r in rows} == {"STANDARD", "PREMIUM", "SENIOR", "CORPORATE", "NEW_TO_BANK"}


def test_labels_follow_scenarios(rows):
    for r in rows:
        assert r.label == (1 if r.scenario in ATTACKS else 0), r.scenario
    assert 0.005 < sum(r.label for r in rows) / len(rows) < 0.05


def test_weights_restore_real_traffic(rows):
    assert all(r.weight > 0 for r in rows)
    assert all(r.weight <= 1 for r in rows if r.label)   # fraud rows are never up-weighted
    share = sum(r.weight for r in rows if r.label) / sum(r.weight for r in rows)
    assert 0.0005 < share < 0.01  # fraud is rare in real traffic, not 1 in 20


def test_features_are_finite_or_missing(rows):
    for r in rows:
        assert set(r.features) == set(FEATURES)
        assert all(v is None or math.isfinite(v) for v in r.features.values()), r.scenario


def test_genuine_rows_overlap_what_fraud_looks_like(rows):
    """The hard negatives: genuine payments with every classic fraud signal on its own."""
    g = [r.features for r in rows if not r.label]
    f = [r.features for r in rows if r.label]
    for name, signal in {
        "huge amount": lambda x: (x["amount_ratio"] or 0) > 20,
        "new device": lambda x: x["device_seen"] == 0 and x["history_count"] > 5,
        "other country": lambda x: x["country_changed"] == 1,
        "recent SIM change": lambda x: (x["sim_changed_hours"] or 1e9) < 24,
        "recent password reset": lambda x: (x["password_reset_hours"] or 1e9) < 24,
        "night": lambda x: x["is_night"] == 1,
        "burst": lambda x: x["velocity_1h"] >= 4,
        "failed checks": lambda x: x["failed_factors_24h"] >= 2,
        "flagged payee": lambda x: x["payee_risk_flag"] == 1,
        "sensitive action": lambda x: x["sensitive_actions_24h"] >= 1,
        "high behaviour risk": lambda x: (x["ueba_risk"] or 0) > .6,
    }.items():
        assert any(signal(x) for x in g), f"no genuine row with: {name}"
        assert any(signal(x) for x in f), f"no fraud row with: {name}"


def test_account_events_and_non_money_actions(rows):
    kinds = {r.scenario: r.features for r in rows}
    # changing a limit or a contact is not a payment: no amount, no payee
    for tag in ("limit_change", "profile_update", "genuine_device_binding"):
        f = kinds[tag]
        assert f["amount_ratio"] is None and f["amount_log"] is None and f["payee_seen"] is None
    assert kinds["bustout_buildup"]["account_age_days"] is not None


def test_incomplete_feeds_blank_exactly_what_they_lack(rows):
    by = {}
    for r in rows:
        by.setdefault(r.feed, []).append(r.features)
    geo = ("place_seen", "place_changed", "country_changed")
    events = ("sim_changed_hours", "contact_changed_hours", "password_reset_hours")
    for feed, lacks_geo, lacks_events, lacks_ueba in (("full", 0, 0, 0), ("no_geo", 1, 0, 0), ("no_events", 0, 1, 0),
                                                      ("no_ueba", 0, 0, 1), ("minimal", 1, 1, 1)):
        fs = by[feed]
        assert all(f["place_seen"] is None and f["country_changed"] is None for f in fs) == bool(lacks_geo)
        assert all(all(f[k] is None for k in events) and f["payee_risk_flag"] == 0 for f in fs) == bool(lacks_events)
        assert all(f["ueba_risk"] is None for f in fs) == bool(lacks_ueba)
        assert any(f[k] is not None for f in fs for k in geo + events) == (not (lacks_geo and lacks_events))


def test_no_activity_before_the_account_exists(rows):
    assert all(r.features["account_age_days"] is None or r.features["account_age_days"] >= 0 for r in rows)
    assert any(r.features["history_count"] == 0 for r in rows)  # cold start is present


def test_same_seed_same_data():
    a, b = dataset(n_customers=60, seed=11), dataset(n_customers=60, seed=11)
    assert [(r.scenario, r.at, r.features) for r in a] == [(r.scenario, r.at, r.features) for r in b]


def test_export_writes_dataset_and_report(rows, tmp_path):
    cov = export.write(rows, tmp_path)
    assert cov["missing_scenarios"] == [] and cov["undocumented_scenarios"] == []
    with open(tmp_path / "dataset.csv") as f:
        header = next(csv.reader(f))
        n = sum(1 for _ in f)
    assert header == export.META + FEATURES and n == len(rows)
    md = (tmp_path / "coverage.md").read_text()
    assert "scam_victim" in md and "new_phone_travel" in md and "no_geo" in md


def test_every_age_group_and_amount_range_is_covered(rows):
    """Each age group has genuine payments across the ranges it plausibly makes; every range has fraud."""
    grid = export.grid(rows)
    assert set(grid["genuine"]) == set(AGE_GROUPS)
    for g in AGE_GROUPS:
        for band in ("<100", "100-1k", "1k-10k", "10k-1L"):   # every age pays small to medium amounts
            assert grid["genuine"][g][band] > 0, (g, band)
        assert sum(grid["fraud"][g].values()) > 0, g          # and every age group is attacked
    for _, _, band in AMOUNT_BANDS:
        assert sum(grid["genuine"][g][band] for g in AGE_GROUPS) > 0, band
        assert sum(grid["fraud"][g][band] for g in AGE_GROUPS) > 0, band


def test_age_is_sensible_and_not_a_model_input(rows):
    assert "age" not in FEATURES and "age_group" not in FEATURES
    for r in rows:
        low, high = AGE_GROUPS[r.age_group].span
        assert 18 <= r.age <= 92 and low <= r.age <= high
        if r.segment == "SENIOR":
            assert r.age >= 60
        if r.features["account_age_days"] is not None:     # nobody's account is older than their adulthood
            assert r.features["account_age_days"] <= (r.age - 17) * 365 + 200 or r.segment == "NEW_TO_BANK"


def test_age_changes_behaviour(rows):
    def rate(group):
        n = [r for r in rows if r.age_group == group and not r.label]
        return len({r.customer for r in n}), len(n)
    young, old = rate("18-24"), rate("75+")
    assert young[1] / young[0] > old[1] / old[0]            # the young transact far more often
    micro = lambda g: sum(r.scenario == "micro_payment" for r in rows if r.age_group == g) / max(1, rate(g)[1])
    assert micro("18-24") > micro("75+")                     # UPI-size payments are a young habit
    assert any(r.scenario == "customer_as_mule" and r.age_group == "18-24" for r in rows)
    assert any(r.scenario == "scam_victim" and r.age_group in ("60-74", "75+") for r in rows)


def test_micro_and_very_large_payments_exist(rows):
    assert any(0 < r.amount < 100 and not r.label for r in rows)
    assert any(r.amount >= 1e7 and not r.label for r in rows) and any(r.amount >= 1e7 and r.label for r in rows)


def test_an_attack_never_predates_the_account_it_uses():
    import random

    from amfa.engine.risk.scenarios import Atk, Customer, START
    for seed in range(300):
        for kind in ("new_account_fraud", "bustout"):
            c = Customer(f"t{seed}", random.Random(seed), 150)
            at = START + random.Random(seed).uniform(1, 149) * 86400
            c.open_account(at, kind)
            a = Atk(c, kind, at)
            assert a.at >= c.created, (kind, seed)


def test_memory_never_goes_beyond_a_year():
    from amfa.engine.risk import features, scenarios
    assert features.HISTORY_DAYS == scenarios.WARMUP_DAYS == 365


def test_repeating_payments_are_in_the_data_and_recognised(rows):
    for tag, floor in (("recurring", .6), ("recurring_quarterly", .6), ("recurring_half_yearly", .6),
                       ("recurring_yearly", .3)):   # a yearly payment is often 366 days after the last: just outside
        fs = [r.features for r in rows if r.scenario == tag]
        assert fs, tag
        known = [f for f in fs if f["payee_seen"] > 0]
        assert len(known) / len(fs) > floor, (tag, len(known), len(fs))
        assert all(f["payee_cycle_error"] is not None for f in known)
    yearly = [r.features for r in rows if r.scenario == "recurring_yearly" and r.features["payee_seen"] > 0]
    assert any(f["payee_cycle_error"] < .02 and .8 < f["amount_vs_payee_avg"] < 1.2 for f in yearly)


def test_the_look_alikes_of_repeating_payments_exist(rows):
    by = lambda tag: [r.features for r in rows if r.scenario == tag]
    scam = by("repeat_scam")   # the same scammer again and again: later payments look like a regular payee
    assert any(f["payee_seen"] >= 3 and .9 < f["amount_vs_payee_avg"] < 1.1 for f in scam)
    assert all(f["payee_seen"] == 0 for f in by("bill_payee_swap"))            # right bill, wrong (new) account
    assert sum(f["amount_vs_payee_avg"] > 5 for f in by("recurring_amount_jump") if f["amount_vs_payee_avg"]) > 0


def test_exposure_includes_the_payment_itself(rows):
    assert all(r.exposure >= r.amount for r in rows if r.features["amount_log"] is not None)
    assert any(r.exposure > r.amount * 1.5 for r in rows)   # and the earlier payments of the day
