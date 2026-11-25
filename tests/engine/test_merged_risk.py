"""The merged risk model: DemoPay's phone and network signals, optional and backward compatible; the port of DemoPay's simulator;
the fraud-family router."""
import json
import math

import numpy as np
import pytest

from amfa.engine.risk import features as F
from amfa.engine.risk.features import CORE_FEATURES, FEATURES, SIGNALS, Event, Profile, build, vector
from amfa.engine.risk.scenarios import ATTACKS
from amfa.engine.risk.upi import convert as C
from amfa.engine.risk.upi import features as UF
from amfa.engine.risk.upi import simulate as US

NOW = 1_780_000_000.0
DAY = F.DAY


def _ev(at, payee="p1", amount=500.0, authorized=True):
    return Event(at=at, hour=12, amount=amount, type="TRANSFER", channel="MOBILE", payee=payee, device="d1", network="n1",
                 authorized=authorized)


def test_signals_are_optional_and_missing_by_default():
    f = build(_ev(NOW), [_ev(NOW - k * DAY) for k in range(1, 10)], Profile())
    assert set(f) == set(FEATURES)
    assert all(f[k] is None for k in SIGNALS)
    assert FEATURES[:len(CORE_FEATURES)] == CORE_FEATURES        # the platform's own inputs keep their order


def test_signals_reach_the_model_as_numbers():
    f = build(_ev(NOW), [], Profile(), {"call_active": True, "call_minutes": 25, "payee_fri": 3, "remote_access_active": None,
                                        "not_a_signal": 1, "channel_qr": "yes"})
    assert f["call_active"] == 1.0 and f["call_minutes"] == 25.0 and f["payee_fri"] == 3.0
    assert f["remote_access_active"] is None and f["channel_qr"] is None and "not_a_signal" not in f


def test_escalating_to_a_new_payee():
    hist = [_ev(NOW - 2 * DAY, "fresh", 1000.0)]
    assert build(_ev(NOW, "fresh", 5000.0), hist, Profile())["escalating_to_payee"] == 1
    assert build(_ev(NOW, "fresh", 500.0), hist, Profile())["escalating_to_payee"] == 0
    old = [_ev(NOW - 20 * DAY, "old", 1000.0)]
    assert build(_ev(NOW, "old", 5000.0), old, Profile())["escalating_to_payee"] == 0     # first paid more than 3 days ago


def test_balance_and_three_day_payees():
    hist = [_ev(NOW - 2 * DAY, "a", 1000.0), _ev(NOW - 1 * DAY, "b", 1000.0), _ev(NOW - 10 * DAY, "c", 1000.0)]
    f = build(_ev(NOW, "d", 5000.0), hist, Profile(), {"balance": 20_000})
    assert f["new_payees_72h"] == 3                                   # a, b and d are new in 3 days; c was paid before
    assert f["amount_over_balance"] == pytest.approx(0.25) and f["outflow_24h_over_balance"] == pytest.approx(0.30)
    assert f["signals_present"] == 0 and "balance" not in f           # the balance is context, not a signal or an input
    g = build(_ev(NOW, "d", 5000.0), hist, Profile())
    assert g["amount_over_balance"] is None and g["new_payees_72h"] == 3


def test_vector_follows_the_models_own_list():
    f = build(_ev(NOW), [], Profile(), {"call_active": 1})
    assert len(vector(f)) == len(FEATURES)
    old = vector(f, CORE_FEATURES)
    assert len(old) == len(CORE_FEATURES) and math.isnan(old[CORE_FEATURES.index("amount_ratio")])


def _write_model(path, names, router=False, experts=False):
    """A tiny but real model written the way train.run writes one."""
    import lightgbm as lgb
    from amfa.engine.risk.scorer import MANIFEST, sha256
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, len(names)))
    y = (X[:, names.index("amount_log")] > 0.5).astype(int)
    lgb.train(dict(objective="binary", verbose=-1, num_leaves=4), lgb.Dataset(X, y), 5).save_model(str(path / "booster_0.txt"))
    files = ["booster_0.txt", "calibration.json", "card.json"]
    card = {"version": "risk-test", "features": names, "historyDays": F.HISTORY_DAYS, "boosters": ["booster_0.txt"],
            "cutoffs": [0.2, 0.4, 0.6, 0.8]}
    if router:
        lgb.train(dict(objective="multiclass", num_class=2, verbose=-1, num_leaves=4), lgb.Dataset(X, y), 5).save_model(str(path / "router.txt"))
        card["router"] = {"file": "router.txt", "classes": ["COACHED", "UNAUTHORISED"]}
        files.append("router.txt")
    if experts:
        card["experts"] = {}
        for kind, col in (("signals", "call_minutes"), ("no_signals", "velocity_1h")):
            yk = (X[:, names.index(col)] > 0).astype(int)
            lgb.train(dict(objective="binary", verbose=-1, num_leaves=4), lgb.Dataset(X, yk), 5).save_model(str(path / f"expert_{kind}_0.txt"))
            card["experts"][kind] = [f"expert_{kind}_0.txt"]
            files.append(f"expert_{kind}_0.txt")
    (path / "calibration.json").write_text(json.dumps({"x": [0, 1], "y": [0, 1]}))
    (path / "card.json").write_text(json.dumps(card))
    (path / MANIFEST).write_text(json.dumps({"files": {n: sha256(path / n) for n in files}}))


def test_a_model_from_before_the_merge_still_loads_and_scores(tmp_path):
    from amfa.engine.risk.scorer import RiskModel
    _write_model(tmp_path, list(CORE_FEATURES))
    m = RiskModel(tmp_path)
    r = m.score(build(_ev(NOW, amount=90_000), [_ev(NOW - k * DAY) for k in range(1, 10)], Profile(), {"call_active": 1}))
    assert r.level in ("T0", "T1", "T2", "T3", "T4") and r.family is None and r.family_probs is None


def test_a_model_needing_features_this_build_lacks_is_refused(tmp_path):
    from amfa.engine.risk.scorer import RiskModel, RiskModelError
    _write_model(tmp_path, list(CORE_FEATURES))
    card = json.loads((tmp_path / "card.json").read_text())
    card["features"] = card["features"] + ["from_the_future"]
    (tmp_path / "card.json").write_text(json.dumps(card))
    from amfa.engine.risk.scorer import MANIFEST, sha256
    man = json.loads((tmp_path / MANIFEST).read_text())
    man["files"]["card.json"] = sha256(tmp_path / "card.json")
    (tmp_path / MANIFEST).write_text(json.dumps(man))
    with pytest.raises(RiskModelError):
        RiskModel(tmp_path)


def test_the_router_names_a_family(tmp_path):
    from amfa.engine.risk.scorer import RiskModel
    _write_model(tmp_path, list(FEATURES), router=True)
    r = RiskModel(tmp_path).score(build(_ev(NOW), [], Profile()))
    assert r.family in ("COACHED", "UNAUTHORISED") and abs(sum(r.family_probs.values()) - 1) < 1e-3


def test_the_port_draws_exactly_demopays_payments():
    """The converted streams are DemoPay's own: featurising them gives build_dataset's rows, number for number."""
    d = US.build_dataset(n_users=12, seed=3)
    rows = []
    for _, _, evs, _ in C.upi_streams(12, 3):
        st = UF.UserState()
        for ts, ev, _meta in evs:
            if ts >= US.T0:
                rows.append(UF.featurize(ev, st))
            st.update(ts, ev.payee, ev.amount, ev.device_id)
    np.testing.assert_allclose(np.asarray(rows, dtype=np.float32), d["X"], equal_nan=True)


def test_every_attack_has_a_family():
    assert all(C.family_of(a) for a in ATTACKS)
    assert all(C.family_of(f"upi:{t}") for t in US.ALL_TYPES)
    assert C.family_of("routine") is None and C.family_of("upi:routine") is None


def test_a_demopay_payment_becomes_platform_inputs():
    _, u, evs, _ = next(C.upi_streams(5, 1))
    ts, ev, _ = evs[-1]
    e, p, sig = C.to_platform(ts, ev, False, u)
    assert e.at == ts and e.amount == ev.amount and set(sig) == set(SIGNALS) | {"balance"}
    f = build(e, [], p, sig)
    assert f["bio_similarity"] == sig["bio_similarity"]


def test_the_plan_carries_the_family_and_the_checks_do_not_change():
    from amfa.engine.context import Band, BehaviouralCtx, Context, DeviceCtx, TransactionCtx
    from amfa.engine.decide import select_by_model
    from amfa.engine.risk.scorer import Result

    class Fake:
        def __init__(self, family):
            self.family = family

        def score(self, features):
            return Result(score=0.4, raw=0.7, level="T3", reasons=[], version="v", family=self.family)

    ctx = Context(userId="u", transactionId="t", transaction=TransactionCtx(amountBand=Band.HIGH, exposureBand=Band.HIGH),
                  device=DeviceCtx(), behavioural=BehaviouralCtx())
    f = build(_ev(NOW, amount=90_000), [], Profile())
    with_family, without = select_by_model(f, ctx, Fake("COACHED")), select_by_model(f, ctx, Fake(None))
    assert with_family.threatFamily == "COACHED" and without.threatFamily is None
    assert with_family.required == without.required and with_family.threatLevel == without.threatLevel


def test_specialists_score_half_and_half(tmp_path):
    """A payment with signals is scored by the general boosters and the "signals" specialist, half each; one without, by the
    general boosters and the "no_signals" specialist; rows in either input order give the same score."""
    import lightgbm as lgb
    from amfa.engine.risk.scorer import RiskModel
    _write_model(tmp_path, list(FEATURES), experts=True)
    m = RiskModel(tmp_path)
    general = lgb.Booster(model_file=str(tmp_path / "booster_0.txt"))
    for sig, kind in (({"call_minutes": 30}, "signals"), (None, "no_signals")):
        f = build(_ev(NOW, amount=5000), [_ev(NOW - k * DAY) for k in range(1, 10)], Profile(), sig)
        x = np.array([vector(f)])
        expert = lgb.Booster(model_file=str(tmp_path / f"expert_{kind}_0.txt"))
        assert m.raw(x)[0] == pytest.approx((general.predict(x)[0] + expert.predict(x)[0]) / 2)
        assert m.raw(np.array([vector(f, m.features)]))[0] == pytest.approx(m.raw(x)[0])
        assert m.score(f).level in ("T0", "T1", "T2", "T3", "T4")


def test_an_old_model_scores_rows_built_in_the_new_order(tmp_path):
    """Scripts that build rows with vector(f) (scripts/evaluate_datasets.py, measure_readiness.py) keep working with an older model."""
    from amfa.engine.risk.scorer import RiskModel
    _write_model(tmp_path, list(CORE_FEATURES))
    m = RiskModel(tmp_path)
    f = build(_ev(NOW), [], Profile())
    assert m.raw(np.array([vector(f)]))[0] == pytest.approx(m.raw(np.array([vector(f, CORE_FEATURES)]))[0])
