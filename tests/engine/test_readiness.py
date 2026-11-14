"""Readiness counts evidence about the customer, not days on the calendar."""
from amfa.engine.risk.readiness import Tracker, wilson_upper

DAY = 86_400.0


def feed(tr, amounts, alarms=None, per_day=1, start=0.0):
    for i, a in enumerate(amounts):
        tr.observe(a, bool(alarms and alarms[i]), start + (i // per_day) * DAY + (i % per_day) * 60)
    return tr


def test_wilson_bound_is_wide_with_little_evidence_and_tightens_with_more():
    assert wilson_upper(0, 0) == 1.0
    assert wilson_upper(0, 5) > .4 > wilson_upper(0, 12) > wilson_upper(0, 20)
    assert wilson_upper(1, 20) < .25 < wilson_upper(1, 10)


def test_a_steady_daily_payer_and_a_steady_weekly_payer_need_the_same_number_of_payments():
    daily = feed(Tracker(), [1500, 1400, 1600] * 5, per_day=1)
    weekly = Tracker()
    for i, a in enumerate([1500, 1400, 1600] * 5):
        weekly.observe(a, False, i * 7 * DAY)
    assert daily.ready() and weekly.ready()          # 15 clean payments: one took two weeks, the other three and a half months


def test_a_burst_in_one_sitting_cannot_buy_readiness():
    burst = feed(Tracker(), [1000] * 40, per_day=40)
    assert burst.proven() and burst.known() and not burst.ready() and burst.why_not() == ["spread"]


def test_a_customer_whose_amounts_swing_widely_needs_more_payments_to_be_known():
    steady = feed(Tracker(), [2000, 2100, 1900, 2050, 1950, 2000, 2100, 1900])
    swinging = feed(Tracker(), [300, 9000, 1200, 45000, 700, 15000, 2500, 80000])
    assert steady.known() and not swinging.known()


def test_false_alarms_on_their_own_payments_delay_readiness():
    clean = feed(Tracker(), [1000] * 15)
    noisy = feed(Tracker(), [1000] * 15, alarms=[i % 4 == 0 for i in range(15)])
    assert clean.ready() and not noisy.ready() and "proven" in noisy.why_not()
