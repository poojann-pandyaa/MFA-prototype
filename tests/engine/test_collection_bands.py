"""Phase 1 of the band switch: the four amount bands, and the bank setting that is off by default and cannot ask for too little."""
import pytest
from pydantic import ValidationError

from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy
from amfa.engine.risk.collection_bands import BANDS, EDGES_MINOR, TOP, band_for_payment, band_of, label

RUPEE = 100


# ---- the bands -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rupees, band", [
    (0, 0), (1_999.99, 0),
    (2_000, 1), (9_999.99, 1),
    (10_000, 2), (99_999.99, 2),
    (1_00_000, 3), (50_00_000, 3), (100_00_00_000, 3),
])
def test_each_amount_lands_in_its_band_and_an_edge_belongs_to_the_band_above(rupees, band):
    assert band_of(round(rupees * RUPEE)) == band


def test_there_are_four_bands_with_the_agreed_edges():
    assert EDGES_MINOR == (2_000_00, 10_000_00, 1_00_000_00)
    assert BANDS == (0, 1, 2, 3) and TOP == 3
    assert [label(b) for b in BANDS] == ["₹0–2,000", "₹2,000–10,000", "₹10,000–1 lakh", "₹1 lakh and above"]


@pytest.mark.parametrize("bad", [None, -1, -10_000_00, 1.5, 2_000.0, "2000", True, False, [], object()])
def test_anything_that_is_not_a_whole_non_negative_amount_is_the_top_band_never_a_small_payment(bad):
    assert band_of(bad) == TOP


# ---- splitting a payment never lowers its band -------------------------------------------------------------------------------

def test_a_payment_split_in_two_is_decided_by_the_band_of_the_day_total():
    # Rs 15,000 sent as two Rs 7,500 payments: the second one carries the day's Rs 15,000
    assert band_of(7_500 * RUPEE) == 1
    assert band_for_payment(7_500 * RUPEE, 15_000 * RUPEE) == 2


def test_a_large_payment_split_in_two_stays_in_the_lakh_band():
    assert band_for_payment(60_000 * RUPEE, 1_20_000 * RUPEE) == 3


def test_a_lone_payment_is_its_own_band():
    assert band_for_payment(1_500 * RUPEE, 1_500 * RUPEE) == 0


def test_a_day_total_smaller_than_the_payment_cannot_lower_it():
    assert band_for_payment(50_000 * RUPEE, 0) == 2


@pytest.mark.parametrize("exposure", [None, -1, "15000", 1.0])
def test_an_unknown_day_total_cannot_rule_out_a_split_so_it_is_the_top_band(exposure):
    assert band_for_payment(500 * RUPEE, exposure) == TOP


# ---- the bank setting ------------------------------------------------------------------------------------------------------

def test_the_band_switch_is_off_by_default_with_the_agreed_threshold():
    c = TenantPolicy().collection
    assert c.enabled is False
    assert (c.gate.minGenuine, c.gate.minFraud, c.gate.minWeeks, c.gate.passingEvaluations) == (5_000, 100, 8, 4)
    assert (c.topGate.minGenuine, c.topGate.minFraud, c.topGate.minWeeks, c.topGate.passingEvaluations) == (10_000, 200, 8, 4)
    assert c.dailyRowsPerCustomer == 10 and c.retentionDays == 365


def test_a_policy_saved_before_the_band_switch_existed_still_loads_with_it_off():
    stored = TenantPolicy().model_dump(mode="json")
    del stored["collection"]
    assert TenantPolicy.model_validate(stored).collection.enabled is False


def test_the_setting_survives_a_save_and_load():
    p = TenantPolicy(collection=CollectionPolicy(enabled=True, gate=BandGate(minGenuine=6_000)))
    again = TenantPolicy.model_validate(p.model_dump(mode="json"))
    assert again.collection == p.collection


@pytest.mark.parametrize("field, too_low", [("minGenuine", 1_999), ("minFraud", 49), ("minWeeks", 3), ("passingEvaluations", 3)])
def test_no_bank_can_ask_for_less_evidence_than_the_hard_minimums(field, too_low):
    with pytest.raises(ValidationError):
        BandGate(**{field: too_low})
    with pytest.raises(ValidationError):
        TenantPolicy.model_validate({"collection": {"gate": {field: too_low}}})
    with pytest.raises(ValidationError):
        TenantPolicy.model_validate({"collection": {"topGate": {field: too_low}}})


def test_the_hard_minimums_themselves_are_accepted():
    g = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4)
    assert TenantPolicy(collection=CollectionPolicy(gate=g, topGate=g)).collection.gate == g


@pytest.mark.parametrize("field, others, top", [("minGenuine", 6_000, 5_999), ("minFraud", 120, 119),
                                                ("minWeeks", 10, 9), ("passingEvaluations", 6, 5)])
def test_the_lakh_band_can_never_ask_for_less_than_the_other_bands(field, others, top):
    # every top-band value is above its hard minimum; it is refused only for being below what bands 0-2 ask
    strong = dict(minGenuine=50_000, minFraud=500, minWeeks=12, passingEvaluations=8)
    with pytest.raises(ValidationError, match="topGate"):
        CollectionPolicy(gate=BandGate(**{field: others}), topGate=BandGate(**(strong | {field: top})))
    CollectionPolicy(gate=BandGate(**{field: others}), topGate=BandGate(**(strong | {field: others})))     # equal is fine


def test_the_lakh_band_may_ask_for_more():
    c = CollectionPolicy(topGate=BandGate(minGenuine=50_000, minFraud=500, minWeeks=12, passingEvaluations=8))
    assert c.topGate.minFraud == 500


@pytest.mark.parametrize("section", [
    {"edgesMinor": [1_000_00, 10_000_00, 1_00_000_00]},          # the edges are not a setting
    {"gate": {"minGenuine": 5_000, "simulatedCounts": True}},     # nor a way to count simulated fraud
    {"surprise": 1},
])
def test_unknown_settings_are_refused_rather_than_silently_ignored(section):
    with pytest.raises(ValidationError):
        TenantPolicy.model_validate({"collection": section})


@pytest.mark.parametrize("field, value", [("dailyRowsPerCustomer", 0), ("dailyRowsPerCustomer", 51),
                                          ("retentionDays", 89), ("retentionDays", 1_826)])
def test_the_daily_cap_and_retention_stay_within_bounds(field, value):
    with pytest.raises(ValidationError):
        CollectionPolicy(**{field: value})


def test_the_band_switch_cannot_be_turned_on_for_a_bank_in_another_currency():
    with pytest.raises(ValidationError, match="INR"):
        TenantPolicy.model_validate({"currency": "EUR", "amountBands": {"EUR": {"mediumFromMinor": 1_000_00, "highFromMinor": 10_000_00}},
                                     "collection": {"enabled": True}})
    # off, it is no obstacle to such a bank
    TenantPolicy.model_validate({"currency": "EUR", "amountBands": {"EUR": {"mediumFromMinor": 1_000_00, "highFromMinor": 10_000_00}}})


def test_the_bank_own_amount_bands_are_untouched():
    p = TenantPolicy(collection=CollectionPolicy(enabled=True))
    assert p.amountBands["INR"].mediumFromMinor == 10_000_00 and p.amountBands["INR"].highFromMinor == 1_00_000_00
