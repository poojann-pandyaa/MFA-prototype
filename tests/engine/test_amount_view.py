"""#1: the option A/B experiment. The simulator can compute the model's inputs from amounts known only by band, training records it,
the live system can never load such a model, and the comparison trains every view on the same customers and tests it on the same
rows. Without a view, nothing changes."""
import importlib.util
import json
import math
from pathlib import Path

import pytest

from amfa.engine.risk import amount_view, scenarios, scorer, train
from amfa.engine.risk.collection_bands import band_of

ROOT = Path(__file__).resolve().parents[2]
B4 = amount_view.get("bands4")
B8 = amount_view.get("bands8")


# ---- the views ---------------------------------------------------------------------------------------------------------------

def test_exact_is_no_change_at_all_and_an_unknown_view_is_refused():
    assert amount_view.get(None) is None and amount_view.get("exact") is None
    with pytest.raises(ValueError):
        amount_view.get("bands5")


def test_each_of_the_four_bands_is_known_by_the_middle_of_the_band_on_a_log_scale():
    assert amount_view.representatives(amount_view.EDGES["bands4"]) == (
        round(math.sqrt(100 * 2_000), 2), round(math.sqrt(2_000 * 10_000), 2),
        round(math.sqrt(10_000 * 1_00_000), 2), round(math.sqrt(1_00_000 * 10_00_000), 2))


@pytest.mark.parametrize("rupees, band", [(0.01, 0), (1_999.99, 0), (2_000, 1), (9_999.99, 1), (10_000, 2), (99_999.99, 2),
                                          (1_00_000, 3), (5_00_00_000, 3)])
def test_the_four_band_view_uses_the_band_switchs_own_edges(rupees, band):
    values = amount_view.representatives(amount_view.EDGES["bands4"])
    assert B4(rupees) == values[band]
    assert band_of(int(round(rupees * 100))) == band               # the same edges as the band switch


def test_amounts_in_one_band_look_identical_and_in_different_bands_do_not():
    assert B4(1_200) == B4(1_900) and B4(12_000) == B4(90_000)
    assert B4(1_999) != B4(2_001)


@pytest.mark.parametrize("amount", [0, 0.0, -5, None])
def test_no_money_moved_stays_zero(amount):
    assert B4(amount) == 0.0 and B8(amount) == 0.0


def test_the_eight_bands_split_the_four_never_cross_them():
    probes = [x * 1.37 for x in range(1, 200_000, 97)]
    for a in probes:
        for b in probes[:50]:
            if B8(a) == B8(b):
                assert B4(a) == B4(b)
    assert len(set(map(B8, probes))) == 8 and len(set(map(B4, probes))) == 4


# ---- the simulator ----------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def exact_rows():
    return scenarios.dataset(n_customers=60, days=150, seed=3)


@pytest.fixture(scope="module")
def banded_rows():
    return scenarios.dataset(n_customers=60, days=150, seed=3, amount_view="bands4")


def _key(r):
    return (r.customer, r.at, r.scenario, r.label)


def test_without_a_view_the_simulator_gives_exactly_what_it_gave_before(exact_rows):
    again = scenarios.dataset(n_customers=60, days=150, seed=3, amount_view="exact")
    assert [(_key(r), r.features, r.amount, r.exposure) for r in again] == \
           [(_key(r), r.features, r.amount, r.exposure) for r in exact_rows]


def test_a_view_keeps_the_same_payments_in_the_same_order(exact_rows, banded_rows):
    assert len(banded_rows) == len(exact_rows) > 500
    assert [_key(r) for r in banded_rows] == [_key(r) for r in exact_rows]


def test_the_row_keeps_the_true_amount_for_counting_money(exact_rows, banded_rows):
    assert [r.amount for r in banded_rows] == [r.amount for r in exact_rows]


def test_the_inputs_are_computed_from_banded_amounts(banded_rows):
    paid = [r for r in banded_rows if r.amount > 0 and r.features.get("amount_log") is not None]
    assert paid
    for r in paid:
        assert r.features["amount_log"] == pytest.approx(math.log1p(B4(r.amount)))


def test_the_24_hour_total_is_banded_too(exact_rows, banded_rows):
    lone = [(e, b) for e, b in zip(exact_rows, banded_rows) if e.amount > 0 and e.exposure == pytest.approx(e.amount)]
    assert lone
    for e, b in lone:
        assert b.exposure == pytest.approx(B4(e.amount))           # a lone payment: its total is its own band's value


def test_inputs_that_do_not_depend_on_amounts_are_unchanged(exact_rows, banded_rows):
    for e, b in zip(exact_rows, banded_rows):
        for k in ("payee_seen", "device_seen", "network_seen", "velocity_1h", "velocity_24h", "hour_deviation", "sim_changed_hours"):
            assert e.features.get(k) == b.features.get(k), k


def test_banding_does_change_the_amount_inputs(exact_rows, banded_rows):
    changed = sum(e.features.get("amount_ratio") != b.features.get("amount_ratio") for e, b in zip(exact_rows, banded_rows))
    assert changed > len(exact_rows) * 0.2


# ---- training and the live guard ----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    out = {}
    for view in ("exact", "bands4"):
        root = tmp_path_factory.mktemp(view)
        out[view] = train.run(customers=300, days=150, seed=3, search=[train.SEARCH[0]], seeds=1, activate=False, force=True,
                              out_root=root, amount_view=view)
    return out


def test_the_model_card_records_the_view(trained):
    for view, path in trained.items():
        assert json.loads((path / "card.json").read_text())["amountView"] == view


def test_the_test_results_break_down_by_the_band_switchs_four_bands(trained):
    card = json.loads((trained["exact"] / "card.json").read_text())
    by_band = card["metrics"]["by_collection_band"]
    assert set(by_band) == {"0", "1", "2", "3"}
    assert sum(v["genuine_rows"] + v["fraud_rows"] for v in by_band.values()) > 0


def test_the_live_system_refuses_a_model_trained_on_bands(trained):
    with pytest.raises(scorer.RiskModelError, match="known only by band"):
        scorer.RiskModel(trained["bands4"])
    assert scorer.RiskModel(trained["bands4"], allow_banded=True).card["amountView"] == "bands4"   # only when asked for
    scorer.RiskModel(trained["exact"])                                                               # exact loads as always


def test_even_made_active_by_hand_a_banded_model_is_never_used(trained, monkeypatch):
    root = trained["bands4"].parent
    (root / "ACTIVE").write_text(trained["bands4"].name)
    monkeypatch.setattr(scorer, "root", lambda: root)
    scorer._load.cache_clear()
    try:
        with pytest.raises(scorer.RiskModelError):
            scorer.active()
    finally:
        scorer._load.cache_clear()


@pytest.mark.parametrize("kw", [{"activate": True}, {"activate": False, "candidate": True}])
def test_training_refuses_to_activate_or_install_a_banded_model(tmp_path, kw):
    with pytest.raises(ValueError, match="comparison only"):
        train.run(customers=50, days=150, seed=3, out_root=tmp_path, amount_view="bands4", **kw)
    assert not list(tmp_path.iterdir())                         # refused before anything was trained or written


# ---- the comparison script ----------------------------------------------------------------------------------------------------

def _script():
    spec = importlib.util.spec_from_file_location("compare_amount_storage", ROOT / "scripts" / "compare_amount_storage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def compared(tmp_path_factory):
    cmp = _script()
    results = cmp.run(customers=300, seeds=[3], views=["exact", "bands4"], full=False, out_root=tmp_path_factory.mktemp("cmp"),
                      progress=lambda *a: None)
    return cmp, results


def test_every_view_of_a_seed_is_tested_on_the_same_rows(compared):
    _, results = compared
    assert results["exact"][3]["test_rows"] == results["bands4"][3]["test_rows"] > 0


def test_the_summary_gives_the_paired_change_from_exact(compared):
    cmp, results = compared
    s = cmp.summary(results)
    assert s["exact"]["pr_auc"]["diff"] is None
    assert s["bands4"]["pr_auc"]["diff"] == pytest.approx(results["bands4"][3]["pr_auc"] - results["exact"][3]["pr_auc"], abs=1e-4)


def test_the_report_has_every_measure_and_band(compared):
    cmp, results = compared
    text = cmp.report(results, cmp.summary(results), 300, [3], ["exact", "bands4"], False)
    for needle in ("Accuracy (PR-AUC)", "Fraud money caught", "Older customers", "₹0–2,000", "₹1 lakh and above", "| exact | bands4 |"):
        assert needle in text, needle


def test_the_comparison_never_touches_the_live_model_store(compared):
    live = scorer.root()
    assert not any(p.name.startswith(("exact-", "bands4-")) for p in live.glob("*")) if live.exists() else True
