# backend/tests/test_ato_model.py
# Account-takeover model: featurizer invariants, a tiny end-to-end training smoke test, the shipped
# artifact's behaviour and card, drift metric, and the /txn endpoint flow (with a stubbed scorer so
# endpoint tests don't depend on model weights).
# NOTE: model-quality assertions are on SYNTHETIC data (see ato/simulate.py).
import datetime
import json
import math
import os

import numpy as np
import pytest

import config
import models
from ato import features as F
from ato import model as ato_model
from ato import train
from services import auth, biometrics

T = 1_790_000_000.0  # arbitrary UTC epoch (~13:30 IST at +hour offsets below)


def _state(n=30, payee_mod=4, amount=300.0, dev="d1"):
    st = F.UserState()
    for i in range(n):
        st.update(T - (n - i) * 86400 - 600, f"reg{i % payee_mod}", amount + i, dev)
    return st


def test_featurize_shape_and_cold_start():
    cold = F.featurize(F.Event(ts=T, payee="x", amount=100, device_id="d1"), F.UserState())
    assert len(cold) == len(F.FEATURES)
    assert math.isnan(cold[F.INDEX["amt_z"]]) and cold[F.INDEX["device_known"]] == 0 and cold[F.INDEX["new_payee"]] == 1


def test_featurize_does_not_mutate_state():
    st = _state()
    before = (st.n, st.mean, st.m2, len(st.recent), dict(st.payee_first))
    F.featurize(F.Event(ts=T, payee="new", amount=5000, device_id="d9"), st)
    assert before == (st.n, st.mean, st.m2, len(st.recent), dict(st.payee_first))


def test_replay_is_deterministic_and_order_matters_only_through_time():
    a, b = _state(), _state()
    ev = F.Event(ts=T, payee="reg1", amount=320, device_id="d1", balance=9000)
    assert F.featurize(ev, a) == F.featurize(ev, b)


def test_known_payee_device_vs_takeover_features():
    st = _state()
    ok = F.featurize(F.Event(ts=T, payee="reg1", amount=310, device_id="d1"), st)
    bad = F.featurize(F.Event(ts=T, payee="mule", amount=9000, device_id="atk", device_bound_hours=1, payee_age_days=1,
                              payee_fan_in_24h=20), st)
    I = F.INDEX
    assert ok[I["new_payee"]] == 0 and ok[I["device_known"]] == 1
    assert bad[I["new_payee"]] == 1 and bad[I["device_known"]] == 0 and bad[I["amt_over_p95"]] > 5


def test_server_and_device_groups_partition_features():
    assert set(F.SERVER_FEATURES) | {f for f, g in F.FEATURE_GROUPS.items() if g == "device"} == set(F.FEATURES)


def test_new_features_lookalike_sequence_and_channel():
    st = _state()
    for i in range(4):  # three new payees in the last 72h, rising amounts to the last one
        st.update(T - 3600 * (30 - i * 5), f"task{i}@ybl", 400.0 * (i + 1), "d1")
    I = F.INDEX
    v = F.featurize(F.Event(ts=T, payee="task3@ybl", amount=5000, device_id="d1", channel="qr"), st)
    assert v[I["new_payees_72h"]] >= 3 and v[I["escalating_to_payee"]] == 1 and v[I["channel_qr"]] == 1
    st2 = F.UserState(); st2.update(T - 86400, "ramesh.kumar@okaxis", 300, "d1")
    near = F.featurize(F.Event(ts=T, payee="ramesh.kumar1@okaxis", amount=300, device_id="d1"), st2)
    far = F.featurize(F.Event(ts=T, payee="zzqxw@ybl", amount=300, device_id="d1"), st2)
    known = F.featurize(F.Event(ts=T, payee="ramesh.kumar@okaxis", amount=300, device_id="d1"), st2)
    assert near[I["payee_lookalike"]] > 0.9 and far[I["payee_lookalike"]] < 0.5 and known[I["payee_lookalike"]] == 0
    col = F.featurize(F.Event(ts=T, payee="x@ybl", amount=1, device_id="d1", channel="collect", requester_is_contact=False), st2)
    pay = F.featurize(F.Event(ts=T, payee="x@ybl", amount=1, device_id="d1", channel="pay", requester_is_contact=False), st2)
    assert col[I["requester_is_contact"]] == 0 and math.isnan(pay[I["requester_is_contact"]])


def test_training_pipeline_smoke():
    card, res, extra = train.run(users=300, seed=11, quick=True, save=False, log=lambda *_: None)
    t = card["variants"]["full"]["thresholds"]
    assert t["MEDIUM"] < t["HIGH"] < t["CRITICAL"]
    assert res["full"]["fpr_HIGH"] <= 0.02
    assert res["full"]["inc_MEDIUM"]["caught"] >= res["rules"]["inc_MEDIUM"]["caught"] - 0.2
    assert extra["latency_ms"] < 100


# ---- shipped artifact -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def scorer():
    s = ato_model.get_scorer()
    if s is None:
        pytest.skip("models/ato artifacts not present (run: python -m ato.train)")
    return s


def _legit_telemetry(**over):
    base = dict(failed_pin_attempts=0, ip_geo_km=3, bio_similarity=0.86, remote_access_active=False, integrity_fail=False,
                overlay_accessibility=False, login_to_pay_s=28, call_active=False, call_minutes=0.0, call_unknown_number=False,
                payee_fri=0, payee_complaints=0, payee_is_merchant=False)
    base.update(over)
    return base


def test_card_matches_features_and_quality_floors(scorer):
    c = scorer.card
    assert c["features"] == F.FEATURES
    full = c["test"]["full"]
    assert full["fpr_HIGH"] <= 0.006                                   # friction budget held out-of-sample
    assert full["inc_HIGH"]["caught"] >= 0.8                            # synthetic data; regression guard only
    assert full["inc_HIGH"]["caught"] > c["test"]["rules"]["inc_HIGH"]["caught"]   # beats the hand-weighted rules
    baseline = json.load(open(__file__.replace("backend/tests/test_ato_model.py", "docs/ato-baseline.json")))
    assert c["by_family"]["takeover"]["caught"] >= baseline["full"]["incidents_caught_HIGH"] - 0.03   # no takeover regression


def test_card_family_routing_and_robustness_floors(scorer):
    c = scorer.card
    assert c["by_family"]["coached"]["caught"] >= 0.85 and c["by_family"]["payee"]["caught"] >= 0.6
    assert c["by_family_server"]["takeover"]["caught"] >= 0.95           # server-only variant still protects against takeover
    assert c["routing"]["takeover"][0] >= 0.9 and c["routing"]["coached"][1] >= 0.9   # action family matches the scam
    stress = c["stress"]
    assert stress["payee intelligence unavailable (age, senders, FRI, complaints missing)"]["caught"] >= 0.6
    assert stress["... same, takeover incidents only"]["caught"] >= 0.95  # takeover is caught from identity signals alone


def test_routine_payment_is_low(scorer):
    st = _state()
    r = scorer.score(F.Event(ts=T, payee="reg1", amount=320, device_id="d1", balance=20000, payee_age_days=900,
                             **_legit_telemetry()), st, attested=True)
    assert r.severity == "LOW" and r.action == "allow"


def test_takeover_is_named_and_gets_identity_action(scorer):
    st = _state()
    r = scorer.score(F.Event(ts=T, payee="mule1@ybl", amount=15000, device_id="atk", balance=20000, payee_age_days=2,
                             payee_fan_in_24h=18, device_bound_hours=1.5, old_device_active=False, sim_changed_hours=2,
                             pin_reset_hours=1, **_legit_telemetry(ip_geo_km=900, bio_similarity=0.3, integrity_fail=True,
                                                                  login_to_pay_s=9, payee_fri=2, payee_complaints=2)), st, attested=True)
    assert r.severity in ("HIGH", "CRITICAL") and r.reasons
    assert r.threat in ("UNAUTHORISED", "PAYEE_RISK")


def test_digital_arrest_pattern_is_coached_and_not_a_face_check(scorer):
    st = _state()
    r = scorer.score(F.Event(ts=T, payee="newguy@ybl", amount=250000, device_id="d1", balance=300000, payee_age_days=3,
                             payee_fan_in_24h=8, **_legit_telemetry(call_active=True, call_minutes=140, call_unknown_number=True,
                                                                    payee_fri=0)), st, attested=True)
    assert r.severity in ("HIGH", "CRITICAL")
    assert r.action != "face_challenge"


def test_collect_request_from_stranger_is_flagged(scorer):
    st = _state()
    r = scorer.score(F.Event(ts=T, payee="refund@ybl", amount=900, device_id="d1", balance=20000, payee_age_days=2,
                             payee_fan_in_24h=12, channel="collect", requester_is_contact=False,
                             **_legit_telemetry(call_active=True, call_minutes=6, call_unknown_number=True)), st, attested=True)
    assert r.severity != "LOW"


def test_lookalike_payee_is_flagged(scorer):
    st = F.UserState()
    for i in range(20):
        st.update(T - (20 - i) * 86400 - 600, "ramesh.kumar@okaxis" if i % 2 else f"shop{i}@ybl", 300.0 + i, "d1")
    r = scorer.score(F.Event(ts=T, payee="ramesh.kumar1@okaxis", amount=400, device_id="d1", balance=20000, payee_age_days=4,
                             payee_fan_in_24h=15, **_legit_telemetry(payee_fri=1)), st, attested=True)
    assert r.severity != "LOW"


def test_fake_seller_payment_to_flagged_young_account_is_flagged(scorer):
    st = _state()
    r = scorer.score(F.Event(ts=T, payee="shopx@ybl", amount=1800, device_id="d1", balance=20000, payee_age_days=2,
                             payee_fan_in_24h=22, channel="qr", **_legit_telemetry(payee_fri=3, payee_complaints=3, payee_is_merchant=True)),
                     st, attested=True)
    assert r.severity != "LOW"


def test_unattested_uses_server_only_and_differs(scorer):
    st = _state()
    ev = F.Event(ts=T, payee="mule1@ybl", amount=15000, device_id="atk", payee_age_days=2, payee_fan_in_24h=18,
                 device_bound_hours=1.5, sim_changed_hours=2, pin_reset_hours=1)
    assert scorer.score(ev, st, attested=False).variant == "server_only"
    assert scorer.score(ev, st, attested=True).variant == "full"


@pytest.mark.xfail(strict=True, reason="Known gap: the FIRST payment on a genuinely new phone gets a face challenge (phone-change is the largest "
                   "false-alarm source in docs/ato-model-report.md). Fix is the vouched-transfer flow, step 4 of docs/implementation-plan.md.")
def test_first_payment_on_genuinely_new_phone_should_get_at_most_a_prompt(scorer):
    st = _state()
    r = scorer.score(F.Event(ts=T, payee="reg1", amount=310, device_id="newphone", balance=20000, payee_age_days=900,
                             device_bound_hours=3, old_device_active=False, **_legit_telemetry()), st, attested=True)
    assert r.severity in ("LOW", "MEDIUM")


def test_second_payment_on_new_phone_gets_at_most_a_prompt(scorer):
    st = _state()
    st.update(T - 1800, "reg1", 300.0, "newphone")  # already paid once from the new phone
    r = scorer.score(F.Event(ts=T, payee="reg1", amount=310, device_id="newphone", balance=20000, payee_age_days=900,
                             device_bound_hours=3, old_device_active=False, **_legit_telemetry()), st, attested=True)
    assert r.severity in ("LOW", "MEDIUM")


def test_evidence_report_matches_the_shipped_model():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "evidence", "results.json")
    if not os.path.exists(path):
        pytest.skip("run: python -m ato.evaluate")
    j = json.load(open(path))
    assert j["main"]["reproduces_card"] is True      # evaluation re-scores the saved model and reproduces the training report
    assert len(j["shifts"]) >= 15                     # the assumption-shift worlds were run


def test_psi_detects_shift():
    base = np.random.default_rng(0).normal(size=5000)
    card = {"feature_deciles": {"x": [float(q) for q in np.quantile(base, np.linspace(0.1, 0.9, 9))]}}
    same = ato_model.psi(card, "x", np.random.default_rng(1).normal(size=5000))
    shifted = ato_model.psi(card, "x", np.random.default_rng(2).normal(loc=1.5, size=5000))
    assert same < 0.05 and shifted > 0.25


# ---- endpoint flow (scorer stubbed) --------------------------------------------------------------
class _Stub:
    def __init__(self, severity, action, threat="UNAUTHORISED"):
        self.r = ato_model.Result(severity, action, 9.0, "full", [("new_payee", 0.4)], threat)

    def score(self, ev, state, attested):
        return self.r


@pytest.fixture()
def authed(client, db_session):
    user = models.User(username="alice", password_hash=auth.get_password_hash("pw"), face_embedding="[]")
    db_session.add(user)
    db_session.commit()
    now = datetime.datetime.utcnow()
    for i in range(12):
        db_session.add(models.Transaction(
            user_id=user.id, payee_vpa=f"friend{i % 3}@upi", amount=300 + i, channel="pay", device_identifier="dev1",
            created_at=now - datetime.timedelta(days=3 + i), status="approved", action="allow", severity="LOW"))
    db_session.commit()
    return {"Authorization": f"Bearer {auth.create_access_token({'sub': 'alice'})}"}


def _risk(client, headers, **kw):
    body = {"payee_vpa": "friend1@upi", "amount": 300, "device_identifier": "dev1"}
    body.update(kw)
    return client.post("/txn/risk", json=body, headers=headers)


def _use(monkeypatch, severity, action, threat="UNAUTHORISED"):
    monkeypatch.setattr(ato_model, "get_scorer", lambda: _Stub(severity, action, threat))


def test_txn_requires_auth(client):
    assert client.post("/txn/risk", json={"payee_vpa": "a@upi", "amount": 1, "device_identifier": "d"}).status_code == 401


def test_low_risk_auto_approved_and_reasons_hidden(client, authed, monkeypatch):
    _use(monkeypatch, "LOW", "allow")
    body = _risk(client, authed).json()
    assert body["status"] == "approved" and body["action"] == "allow" and "reasons" not in body


def test_real_model_end_to_end_routine_payment_allowed(client, authed):
    if ato_model.get_scorer() is None:
        pytest.skip("no artifacts")
    assert _risk(client, authed).json()["action"] == "allow"


def test_face_challenge_gates_confirm_until_step2_passes(client, authed, monkeypatch):
    _use(monkeypatch, "HIGH", "face_challenge")
    body = _risk(client, authed, payee_vpa="new@upi", amount=5000).json()
    assert body["action"] == "face_challenge" and body["step_up"]["session_id"]
    assert client.post(f"/txn/{body['txn_id']}/confirm", headers=authed).status_code == 403
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)
    s2 = client.post("/login/step2", json={"username": "alice", "images": ["x"], "device_identifier": "dev1",
                                           "session_id": body["step_up"]["session_id"]})
    assert s2.status_code == 200
    assert client.post(f"/txn/{body['txn_id']}/confirm", headers=authed).json()["status"] == "approved"


def test_critical_is_blocked_and_cannot_be_confirmed(client, authed, monkeypatch):
    _use(monkeypatch, "CRITICAL", "block_and_lock_binding")
    body = _risk(client, authed).json()
    assert body["status"] == "blocked"
    assert client.post(f"/txn/{body['txn_id']}/confirm", headers=authed).status_code == 403


def test_step2_for_payment_does_not_touch_login_history(client, authed, db_session, monkeypatch):
    _use(monkeypatch, "HIGH", "face_challenge")
    body = _risk(client, authed, payee_vpa="new@upi").json()
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)
    client.post("/login/step2", json={"username": "alice", "images": ["x"], "device_identifier": "dev1",
                                      "session_id": body["step_up"]["session_id"]})
    assert db_session.query(models.LoginHistory).count() == 0
    assert db_session.query(models.Device).count() == 0


def test_shadow_mode_logs_but_never_adds_friction(client, authed, db_session, monkeypatch):
    _use(monkeypatch, "CRITICAL", "block_and_lock_binding")
    monkeypatch.setattr(config, "ATO_SHADOW", True)
    body = _risk(client, authed).json()
    assert body["action"] == "allow" and body["status"] == "approved" and body["severity"] == "CRITICAL"
    row = db_session.get(models.Transaction, body["txn_id"])
    assert "shadow:block_and_lock_binding" in json.loads(row.reasons)


def test_unattested_request_drops_device_telemetry(client, authed, monkeypatch):
    seen = {}

    class Spy:
        def score(self, ev, state, attested):
            seen["ev"], seen["attested"] = ev, attested
            return ato_model.Result("LOW", "allow", 0.0, "server_only", [], "NONE")

    monkeypatch.setattr(ato_model, "get_scorer", lambda: Spy())
    _risk(client, authed, remote_access_active=False, bio_similarity=0.99, call_active=True, requester_is_contact=False, sim_changed_hours=3)
    ev = seen["ev"]
    assert seen["attested"] is False and ev.bio_similarity is None and ev.call_active is None and ev.requester_is_contact is None
    assert ev.sim_changed_hours == 3


def test_history_replay_reaches_the_scorer(client, authed, monkeypatch):
    seen = {}

    class Spy:
        def score(self, ev, state, attested):
            seen["n"], seen["known"] = state.n, ev.payee in state.payee_first
            return ato_model.Result("LOW", "allow", 0.0, "full", [], "NONE")

    monkeypatch.setattr(ato_model, "get_scorer", lambda: Spy())
    _risk(client, authed)
    assert seen["n"] == 12 and seen["known"] is True


def test_cooling_off_hold_blocks_confirm_until_released(client, authed, db_session, monkeypatch):
    _use(monkeypatch, "HIGH", "cooling_off_hold", "COACHED")
    body = _risk(client, authed, payee_vpa="new@upi", amount=4000).json()
    assert body["action"] == "cooling_off_hold" and body["hold_until"] and body["threat_type"] == "COACHED"
    assert client.post(f"/txn/{body['txn_id']}/confirm", headers=authed).status_code == 409
    txn = db_session.get(models.Transaction, body["txn_id"])
    txn.release_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    db_session.commit()
    assert client.post(f"/txn/{body['txn_id']}/confirm", headers=authed).json()["status"] == "approved"


def test_collect_request_warning_text_and_fri_mapping(client, authed, monkeypatch):
    seen = {}

    class Spy:
        def score(self, ev, state, attested):
            seen["fri"], seen["channel"] = ev.payee_fri, ev.channel
            return ato_model.Result("MEDIUM", "scam_warning", 5.0, "full", [], "COACHED")

    monkeypatch.setattr(ato_model, "get_scorer", lambda: Spy())
    body = _risk(client, authed, channel="collect", sdk_attested=True, requester_is_contact=False, payee_fri="very_high").json()
    assert seen["fri"] == 3 and seen["channel"] == "collect" and "SEND money" in body["message"]


def test_fallback_rules_when_no_artifacts(client, authed, monkeypatch):
    monkeypatch.setattr(ato_model, "get_scorer", lambda: None)
    body = _risk(client, authed, sdk_attested=True, remote_access_active=True).json()
    assert body["severity"] == "HIGH" and body["action"] == "face_challenge"
