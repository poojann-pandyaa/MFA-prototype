"""End-to-end API flow: risk check -> plan -> steps -> PIN -> money moves (risk model stubbed unless a test says otherwise)."""
import pytest

from app import config
from ato import model as ato_model

SCAM_PAYEE = "refund.desk@ybl"
FRIEND = "priya.sharma@okaxis"


def risk(client, auth, payee=SCAM_PAYEE, amount=4000, **extra):
    r = client.post("/pay/risk", json={"payee_vpa": payee, "amount": amount, **extra}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def answer_for(client, auth, pid, step):
    """Start a step and give the honest, correct answer for it."""
    n, method = step["index"], step["method"]
    s = client.post(f"/pay/{pid}/steps/{n}/start", headers=auth)
    assert s.status_code == 200, s.text
    sd = s.json()
    body = {"nonce": sd["nonce"]}
    if method == "warning":
        body.update(acknowledged=True, answers={q["id"]: "no" for q in sd["data"]["questions"]})
    elif method == "typed_confirm":
        body["typed"] = client.get("/directory/lookup", params={"vpa": sd["data"]["payee_vpa"]}, headers=auth).json()["registered_name"]
    elif method == "payee_check":
        body["confirmed"] = True
    return client.post(f"/pay/{pid}/steps/{n}/answer", json=body, headers=auth)


def finish_steps(client, auth, p):
    for step in p["plan"]["steps"]:
        r = answer_for(client, auth, p["id"], step)
        assert r.status_code == 200 and r.json()["passed"], r.text


def test_low_risk_payment_goes_straight_to_pin_and_moves_money(client, auth, stub):
    stub("LOW", "NONE")
    p = risk(client, auth, FRIEND, 300)
    assert p["status"] == "approved" and p["plan"]["decision"] == "allow" and p["plan"]["steps"] == []
    r = client.post(f"/pay/{p['id']}/complete", json={"pin": "1234"}, headers=auth)
    assert r.status_code == 200 and r.json()["balance"] == config.OPENING_BALANCE - 300
    assert client.post(f"/pay/{p['id']}/complete", json={"pin": "1234"}, headers=auth).status_code == 409   # cannot pay twice


def test_money_cannot_move_until_every_step_passes(client, auth, stub):
    stub("HIGH", "COACHED")
    p = risk(client, auth, amount=80_000, base_rate=1e-4)
    assert p["status"] == "pending" and len(p["plan"]["steps"]) >= 1
    assert client.post(f"/pay/{p['id']}/complete", json={"pin": "1234"}, headers=auth).status_code == 409
    finish_steps(client, auth, p)
    done = client.get(f"/pay/{p['id']}", headers=auth).json()
    assert done["status"] == "approved"
    assert client.post(f"/pay/{p['id']}/complete", json={"pin": "123456"}, headers=auth).json()["balance"] == config.OPENING_BALANCE - 80_000


def test_a_danger_answer_to_a_warning_question_stops_the_payment(client, auth, stub):
    stub("MEDIUM", "COACHED")
    p = risk(client, auth, amount=9_000, base_rate=1e-3)       # at the default 1-in-10,000 rate a MEDIUM alert on Rs 9,000 is simply allowed
    assert [s["method"] for s in p["plan"]["steps"]] == ["warning"]
    sd = client.post(f"/pay/{p['id']}/steps/0/start", headers=auth).json()
    answers = {q["id"]: "yes" for q in sd["data"]["questions"]}
    r = client.post(f"/pay/{p['id']}/steps/0/answer", json={"nonce": sd["nonce"], "acknowledged": True, "answers": answers}, headers=auth).json()
    assert r["blocked"] and "1930" in r["message"]
    assert client.post(f"/pay/{p['id']}/complete", json={"pin": "1234"}, headers=auth).status_code == 409


def test_wrong_nonce_is_rejected(client, auth, stub):
    stub("MEDIUM", "COACHED")
    p = risk(client, auth, amount=9_000, base_rate=1e-3)
    client.post(f"/pay/{p['id']}/steps/0/start", headers=auth)
    r = client.post(f"/pay/{p['id']}/steps/0/answer", json={"nonce": "nope", "acknowledged": True, "answers": {}}, headers=auth)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "mismatch"


def test_steps_must_be_done_in_order_and_started_first(client, auth, stub):
    stub("HIGH", "COACHED")
    p = risk(client, auth, amount=80_000)
    assert len(p["plan"]["steps"]) >= 2
    assert client.post(f"/pay/{p['id']}/steps/1/start", headers=auth).status_code == 409
    assert client.post(f"/pay/{p['id']}/steps/0/answer", json={"nonce": "x"}, headers=auth).status_code == 409   # not started
    finish_steps(client, auth, p)
    assert client.post(f"/pay/{p['id']}/steps/0/start", headers=auth).status_code == 409                      # already done


def test_typed_confirmation_retries_then_locks(client, auth, stub, monkeypatch):
    stub("HIGH", "PAYEE_RISK")
    p = risk(client, auth, amount=3_000, base_rate=1e-3)
    step = next((s for s in p["plan"]["steps"] if s["method"] == "typed_confirm"), None)
    if step is None:
        pytest.skip("engine did not choose typed confirmation for this input")
    for done in p["plan"]["steps"][: step["index"]]:
        assert answer_for(client, auth, p["id"], done).json()["passed"]
    sd = client.post(f"/pay/{p['id']}/steps/{step['index']}/start", headers=auth).json()
    last = None
    for _ in range(config.MAX_ATTEMPTS):
        last = client.post(f"/pay/{p['id']}/steps/{step['index']}/answer", json={"nonce": sd["nonce"], "typed": "Wrong Name"}, headers=auth).json()
    assert last["blocked"]
    assert client.get(f"/pay/{p['id']}", headers=auth).json()["status"] == "blocked"


def test_cooling_off_wait_cannot_be_skipped(client, auth, stub, monkeypatch):
    monkeypatch.setattr(config, "HOLD_SECONDS", 60)
    stub("HIGH", "COACHED")
    p = risk(client, auth, amount=80_000, base_rate=1e-4)
    step = next((s for s in p["plan"]["steps"] if s["method"] == "cooling_off"), None)
    if step is None:
        pytest.skip("engine did not choose the wait for this input")
    for done in p["plan"]["steps"][: step["index"]]:
        assert answer_for(client, auth, p["id"], done).json()["passed"]
    sd = client.post(f"/pay/{p['id']}/steps/{step['index']}/start", headers=auth).json()
    assert sd["data"]["shortened_for_demo"] is True
    r = client.post(f"/pay/{p['id']}/steps/{step['index']}/answer", json={"nonce": sd["nonce"]}, headers=auth).json()
    assert r["wait"] is True and r["status"] == "pending"


def test_blocked_payment_by_the_engine_cannot_be_paid(client, auth, stub):
    stub("CRITICAL", "UNAUTHORISED")
    p = risk(client, auth, FRIEND, 80_000, base_rate=1e-3)
    assert p["status"] == "blocked" and "1930" in p["message"]
    assert client.post(f"/pay/{p['id']}/complete", json={"pin": "1234"}, headers=auth).status_code == 409


def test_unknown_payee_never_gets_the_typed_name_step(client, auth, stub):
    stub("HIGH", "PAYEE_RISK")
    p = risk(client, auth, "someone.new@ybl", 3_000)
    assert "typed_confirm" not in [s["method"] for s in p["plan"]["steps"]]


def test_cancel_and_balance_checks(client, auth, stub):
    stub("MEDIUM", "COACHED")
    p = risk(client, auth, amount=9_000)
    assert client.post(f"/pay/{p['id']}/cancel", headers=auth).json()["status"] == "cancelled"
    assert client.post(f"/pay/{p['id']}/cancel", headers=auth).status_code == 409
    assert client.post("/pay/risk", json={"payee_vpa": FRIEND, "amount": config.OPENING_BALANCE + 1}, headers=auth).status_code == 400


def test_payments_are_private_to_their_owner(client, auth, other, stub):
    stub("LOW", "NONE")
    p = risk(client, auth, FRIEND, 100)
    assert client.get(f"/pay/{p['id']}", headers=other).status_code == 404
    assert client.get(f"/demo/xray/{p['id']}", headers=other).status_code == 404
    assert client.post(f"/pay/{p['id']}/complete", json={"pin": "1234"}, headers=other).status_code == 404


def test_xray_is_demo_only_and_never_in_the_normal_payment_response(client, auth, stub, monkeypatch):
    stub("HIGH", "COACHED")
    p = risk(client, auth, amount=4_000)
    assert "explain" not in p and "reasons" not in p and "surprises" not in p
    x = client.get(f"/demo/xray/{p['id']}", headers=auth).json()
    assert x["severity"] == "HIGH" and x["plan"]["decision"] in ("steps", "block") and "surprises" in x
    monkeypatch.setattr(config, "DEMO", False)
    assert client.get(f"/demo/xray/{p['id']}", headers=auth).status_code == 404
    assert client.get("/demo/directory", headers=auth).status_code == 404


def test_pin_must_be_numeric(client, auth, stub):
    stub("LOW", "NONE")
    p = risk(client, auth, FRIEND, 100)
    assert client.post(f"/pay/{p['id']}/complete", json={"pin": "12ab"}, headers=auth).status_code == 422


# ---- real model, real history ---------------------------------------------------------------------------------
@pytest.fixture()
def real_model():
    if ato_model.get_scorer() is None:
        pytest.skip("risk model files not found")


def test_real_model_routine_payment_for_a_loaded_persona_is_allowed(client, auth, real_model):
    assert client.post("/demo/persona", json={"name": "asha"}, headers=auth).status_code == 200
    p = risk(client, auth, "kirana.store@paytm", 380, base_rate=1e-4)
    assert p["plan"]["decision"] == "allow", p


def test_real_model_scam_like_payment_is_not_waved_through(client, auth, real_model):
    client.post("/demo/persona", json={"name": "ramesh"}, headers=auth)
    p = risk(client, auth, "officer.verify@upi", 90_000, base_rate=1e-4,
             signals={"call_active": True, "call_minutes": 95, "call_unknown_number": True})
    assert p["severity"] in ("HIGH", "CRITICAL"), p
    assert p["plan"]["decision"] != "allow"


def test_persona_loading_replaces_history(client, auth):
    r = client.post("/demo/persona", json={"name": "meera"}, headers=auth).json()
    assert r["payments"] == 3
    r2 = client.post("/demo/persona", json={"name": "asha"}, headers=auth).json()
    assert r2["payments"] == 70
    assert len(client.get("/pay", headers=auth).json()) == 40      # activity list is capped


def test_activity_list_is_newest_first_even_for_loaded_history(client, auth):
    client.post("/demo/persona", json={"name": "asha"}, headers=auth)
    rows = client.get("/pay", headers=auth).json()
    stamps = [r["created_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
