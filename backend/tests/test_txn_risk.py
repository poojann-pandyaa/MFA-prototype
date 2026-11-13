# backend/tests/test_txn_risk.py
# Legacy rule scorer (services/txn_risk.py) and its simulator. The /txn endpoint now uses the
# trained ATO model instead; see tests/test_ato_model.py.
# NOTE: the simulator thresholds are a regression guard on hand-set weights, not
# evidence of real-world accuracy (see tools/fraud_sim.py docstring).
import datetime

from services import txn_risk
from services.txn_risk import Txn, assess, build_profile
from tools import fraud_sim

NOW = datetime.datetime(2026, 11, 6, 8, 0)  # 13:30 IST
HISTORY = [(f"friend{i % 4}@upi", 300.0 + 10 * i, NOW - datetime.timedelta(days=2 + i)) for i in range(30)]
PROFILE = build_profile(HISTORY, NOW)


def _assess(**kw):
    return assess(Txn(payee_vpa=kw.pop("payee_vpa", "stranger@upi"), amount=kw.pop("amount", 400.0), now=NOW, **kw), PROFILE)


def test_routine_payment_is_allowed():
    d = _assess(payee_vpa="friend1@upi", amount=350)
    assert (d.severity, d.action) == ("LOW", "allow")


def test_collect_request_from_stranger_gets_send_money_warning():
    d = _assess(channel="collect", amount=800)
    assert d.threat_type == "COACHED" and d.action in ("scam_warning", "cooling_off_hold")
    assert "SEND money" in txn_risk.user_message(d) or d.action == "cooling_off_hold"


def test_digital_arrest_pattern_is_blocked_not_face_checked():
    d = _assess(amount=250000, balance=300000, call_active=True, call_minutes=120, call_unknown_number=True)
    assert d.threat_type == "COACHED" and d.severity == "CRITICAL" and d.action == "block_and_offer_report"


def test_sim_swap_plus_new_binding_triggers_face_challenge():
    d = _assess(amount=5000, sim_changed_hours=3, device_bound_hours=1)
    assert d.threat_type == "UNAUTHORISED" and d.action in ("face_challenge", "block_and_lock_binding")


def test_remote_access_alone_is_at_least_high():
    d = _assess(payee_vpa="friend1@upi", amount=300, remote_access_active=True)
    assert d.severity in ("HIGH", "CRITICAL")


def test_very_high_fri_blocks_regardless_of_user_history():
    d = _assess(payee_vpa="friend1@upi", amount=300, payee_fri="very_high")
    assert d.threat_type == "PAYEE_RISK" and d.action == "block"


def test_escalating_payments_to_recent_payee_flagged():
    rows = HISTORY + [("task@upi", 500.0, NOW - datetime.timedelta(hours=30)), ("task@upi", 900.0, NOW - datetime.timedelta(hours=10))]
    d = assess(Txn(payee_vpa="task@upi", amount=1800, now=NOW, payee_category="crypto"), build_profile(rows, NOW))
    assert d.threat_type == "COACHED" and d.severity in ("MEDIUM", "HIGH", "CRITICAL")


def test_simulator_regression_guard():
    s = fraud_sim.summarise(fraud_sim.run(users=120, seed=1))
    assert s["legit_friction_rate"] <= 0.02   # >= HIGH on legit payments (face check / hold / block)
    assert s["legit_flag_rate"] <= 0.10       # >= MEDIUM
    for name in ("S1 collect request", "S4 remote access", "S5 malicious APK binding", "S6 SIM swap", "S7 digital arrest"):
        assert s["scam_recall"][name][0] >= 0.85, name
