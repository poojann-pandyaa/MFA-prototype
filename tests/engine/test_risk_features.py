"""The shared feature builder: what counts as history, and what is missing vs zero."""
import math

from amfa.engine.risk.features import (CATEGORICAL, DAY, FEATURES, H, MONOTONE, Event, Profile, build,
                                       vector)

NOW = 1_780_000_000.0


def ev(ago_h, *, payee="p1", device="d1", network="n1", place="IN:Pune", amount=1000.0, ok=True, **kw):
    return Event(at=NOW - ago_h * H, hour=14, amount=amount, type="TRANSFER", channel="MOBILE", payee=payee,
                 device=device, network=network, place=place, country=place.split(":")[0] if place else None,
                 authorized=ok, **kw)


def test_no_history_is_missing_not_zero():
    f = build(ev(0), [], Profile())
    assert f["amount_ratio"] is None and f["payee_age_days"] is None and f["place_changed"] is None
    assert f["history_count"] == 0 and f["payee_seen"] == 0 and f["new_payees_24h"] == 1


def test_known_things_come_only_from_authorized_history():
    hist = [ev(48), ev(30, ok=False, device="d2"), ev(26)]
    f = build(ev(0, device="d2"), hist, Profile())
    assert f["payee_seen"] == 2 and f["device_seen"] == 0  # the unauthorized d2 attempt teaches nothing
    assert f["history_count"] == 2 and f["amount_ratio"] == 1.0
    assert f["velocity_24h"] == 0 and f["velocity_1h"] == 0


def test_velocity_counts_every_attempt_and_window_limits_history():
    # too old (the window is 365 days), a recent failure, recent, in the future
    hist = [ev(24 * 400), ev(0.5, ok=False), ev(2), ev(-1)]
    f = build(ev(0), hist, Profile())
    assert f["velocity_1h"] == 1 and f["velocity_24h"] == 2
    assert f["history_count"] == 1


def test_new_payees_today_and_location_change():
    hist = [ev(72, payee="old"), ev(3, payee="a", place="IN:Delhi"), ev(2, payee="b", place="IN:Delhi")]
    f = build(ev(0, payee="c", place="AE:Dubai"), hist, Profile())
    assert f["new_payees_24h"] == 3
    assert f["place_changed"] == 1 and f["country_changed"] == 1
    assert math.isclose(f["minutes_since_last"], 120)


def test_account_events_only_count_if_they_already_happened():
    p = Profile(sim_changed_at=NOW - 3 * H, password_reset_at=NOW + H, payee_risk_flag=True)
    f = build(ev(0), [], p)
    assert math.isclose(f["sim_changed_hours"], 3) and f["password_reset_hours"] is None
    assert f["payee_risk_flag"] == 1


def test_amount_ratios_against_median_and_max():
    hist = [ev(24 * d, amount=a) for d, a in ((5, 500.0), (4, 1000.0), (3, 2000.0))]
    f = build(ev(0, amount=20000.0), hist, Profile())
    assert f["amount_ratio"] == 20.0 and f["amount_vs_max"] == 10.0


def test_schema():
    assert set(MONOTONE) <= set(FEATURES) and set(CATEGORICAL) <= set(FEATURES)
    v = vector(build(ev(0), [], Profile()))
    assert len(v) == len(FEATURES) and math.isnan(v[FEATURES.index("amount_ratio")])
    assert build(ev(0), [], Profile(segment="UNKNOWN"))["segment"] is None
    assert DAY == 24 * H


def test_account_actions_are_not_payments():
    """Changing a limit or adding a payee must not look like a payment of Rs 0 or pollute payee stats."""
    def act(ago_h, type_, payee="p1", ok=True):
        return Event(at=NOW - ago_h * H, hour=14, amount=0.0, type=type_, channel="MOBILE", payee=payee,
                     device="d1", network="n1", place="IN:Pune", country="IN", authorized=ok)
    hist = [ev(48), ev(30), act(5, "CHANGE_LIMIT", payee="limit"), act(3, "ADD_BENEFICIARY", payee="new")]
    f = build(ev(0, payee="new"), hist, Profile())
    assert f["sensitive_actions_24h"] == 2
    assert f["amount_ratio"] == 1.0 and f["history_count"] == 2   # the limit change is not a payment
    assert f["payee_seen"] == 1 and f["new_payees_24h"] == 1       # "new" was added, "limit" is no payee
    own = build(act(0, "PROFILE_CHANGE", payee="profile"), hist, Profile())
    assert own["amount_ratio"] is None and own["amount_log"] is None and own["payee_seen"] is None
    assert own["new_payees_24h"] == 1  # only the payee added earlier today


def test_extreme_and_degenerate_inputs_stay_finite():
    huge = Event(at=NOW, hour=0, amount=1e13, type="TRANSFER", channel="API", payee="", device="", network="",
                 place=None, country=None)
    zero = [ev(a, amount=0.0) for a in (3, 2, 1)]                                    # all-zero history
    same = [ev(1) for _ in range(5)]                                                  # identical timestamps
    other = [ev(1, place=None), ev(2, device="")]
    for hist in ([], zero, same, other, [ev(0), ev(-5)]):                             # now / the future
        for cur in (huge, ev(0, amount=0.0), ev(0, amount=1e13)):
            f = build(cur, hist, Profile(segment="UNKNOWN", ueba_risk=1.0))
            assert all(v is None or math.isfinite(v) for v in f.values()), (cur.amount, f)
    assert build(huge, [], Profile())["hour_deviation"] is None
    assert build(ev(0), same, Profile())["velocity_1h"] == 5


def test_hour_deviation_reads_the_customers_usual_time():
    from amfa.engine.risk.features import hour_deviation
    usual = [20] * 12
    assert hour_deviation(20, usual) < 1e-9
    assert hour_deviation(8, usual) > hour_deviation(22, usual) > 0   # 12 h away is odder than 2 h away
    assert hour_deviation(3, [3, 15] * 6) < 4                          # no usual time -> little to deviate from
    assert hour_deviation(8, usual[:3]) is None                        # too little history to say


def test_repeat_payments_are_recognised_within_the_year():
    """Half-yearly rent: paid 363 and 182 days ago, same amount, now due again."""
    hist = [ev(24 * 363, payee="landlord", amount=30000.0), ev(24 * 182, payee="landlord", amount=30000.0),
            ev(24 * 50, payee="other")]
    f = build(ev(0, payee="landlord", amount=30000.0), hist, Profile())
    assert f["payee_seen"] == 2 and math.isclose(f["days_since_payee_last_paid"], 182)
    assert f["amount_vs_payee_avg"] == 1.0 and math.isclose(f["payee_gap_ratio"], 1.0, abs_tol=.01)
    assert f["payee_cycle_error"] < .01                 # 182 days is exactly a half-yearly cycle


def test_the_same_payee_at_a_far_bigger_amount_stands_out():
    hist = [ev(24 * 60, payee="landlord", amount=30000.0), ev(24 * 30, payee="landlord", amount=30000.0)]
    assert build(ev(0, payee="landlord", amount=900000.0), hist, Profile())["amount_vs_payee_avg"] == 30.0


def test_memory_stops_at_365_days():
    from amfa.engine.risk.features import HISTORY_DAYS
    assert HISTORY_DAYS == 365
    f = build(ev(0, payee="landlord"), [ev(24 * 366, payee="landlord")], Profile())   # a year and a day ago
    assert f["payee_seen"] == 0 and f["days_since_payee_last_paid"] is None and f["payee_cycle_error"] is None
    assert build(ev(0, payee="landlord"), [ev(24 * 364, payee="landlord")], Profile())["payee_seen"] == 1


def test_a_new_payee_has_no_repeat_signals():
    f = build(ev(0, payee="stranger"), [ev(24 * 10, payee="other")], Profile())
    assert f["payee_seen"] == 0 and f["amount_vs_payee_avg"] is None and f["payee_gap_ratio"] is None
