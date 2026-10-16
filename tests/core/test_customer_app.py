"""End to end: the Demo Bank customer app running against a real in-process core. Each test drives the
bank's API like the phone app would, completes the checks AMFA asks for through the client API, and checks
the bank only acts on a verified, consumed proof bound to that exact action. The risk model is a stub whose
threat level each test sets; the biometric services are scripted; OTP codes are read from the delivery."""
import importlib
import os
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from amfa import config
from amfa.common.contract import FactorResult
from amfa.core import api_admin, db, factors, orchestrator, otp
from amfa.core.app import create_app
from amfa.core.auth import DEFAULT_SCOPES
from amfa.engine.risk import scorer

PHONE_DEVICE = "device-phone-0001"
LARGE = 20_000   # rupees: above the bank's LOW band, so the small-payment cap keeps the model's level
LEVEL = {"now": "T1"}
SENT: list[str] = []


@pytest.fixture(scope="module")
def stack():
    base = config.get().public_url
    with TestClient(create_app(), base_url=base) as core:
        tenant = api_admin.create_tenant("Demo Bank", f"demo{uuid.uuid4().hex[:6]}", "test")
        key = api_admin.create_api_key(tenant["id"], "bank", list(DEFAULT_SCOPES), "test")
        os.environ.update(AMFA_BASE_URL=base, AMFA_KEY_ID=key["keyId"], AMFA_KEY_SECRET=key["secret"],
                          AMFA_TENANT_ID=tenant["id"], BANK_SESSION_SECRET="test-session-secret")
        os.environ.pop("BANK_STATE_FILE", None)
        bank = importlib.import_module("examples.demo_bank.bank")
        bank.AMFA = bank.AmfaClient(base, key["keyId"], key["secret"], http=core)
        yield SimpleNamespace(core=core, bank=bank, tenant=tenant)


@pytest.fixture(autouse=True)
def scripted(monkeypatch):
    LEVEL["now"] = "T1"
    monkeypatch.setattr(scorer, "active", lambda: SimpleNamespace(score=lambda f: scorer.Result(
        score=0.5, raw=0.5, level=LEVEL["now"], reasons=[("payee_seen", 0.2), ("spoof_7d", 0.1)], version="stub")))
    monkeypatch.setattr(factors, "ueba", lambda path, body: {"riskScore": 0.2, "band": "LOW", "status": "OK",
                                                              "dimensionsUsed": [], "latencyMs": 1})
    monkeypatch.setattr(factors, "verify", lambda factor, **kw: FactorResult(
        factor=factor, status="PASSED", score=0.9, threshold=0.5))
    monkeypatch.setattr(otp, "deliver", lambda contact, message: SENT.append(message) or "sms")


@pytest.fixture
def app(stack):
    """A signed-in, enrolled customer on their own trusted phone."""
    client = TestClient(stack.bank.app)
    user = f"u{uuid.uuid4().hex[:8]}"
    r = client.post("/api/register", json={"username": user, "name": "Asha Rao", "password": "long-password-1",
                                           "phone": "+919812345678", "deviceId": PHONE_DEVICE})
    assert r.status_code == 201
    client.post("/api/amfa/enrol").raise_for_status()
    amfa_user = stack.bank.CUSTOMERS[user]["amfaUser"]
    with db.session() as s:
        for f in ("FACE", "VOICE", "UEBA"):
            s.add(db.Enrollment(customer_id=amfa_user, factor=f))
    client.user = user
    return client


def act(app, kind, device=PHONE_DEVICE, **body):
    return app.post(f"/api/actions/{kind}", json={"deviceId": device, **body})


def verify(stack, challenge_url):
    """Complete every check in the session, as the hosted challenge page would."""
    token = challenge_url.split("#token=", 1)[1]
    auth = {"authorization": f"Bearer {token}"}
    for _ in range(8):
        state = stack.core.get("/c/v1/session", headers=auth).json()
        ch = state["challenge"]
        if ch is None:
            return state
        payload = {"code": re.match(r"(\d+)", SENT[-1]).group(1)} if ch["factor"] == "OTP" else {}
        stack.core.post(f"/c/v1/session/factors/{ch['factor']}", headers=auth,
                        json={"challengeId": ch["challengeId"], "payload": payload}).raise_for_status()
    raise AssertionError("session never settled")


def balance(app):
    return app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["balance"]


def test_transfer_runs_only_after_the_checks_and_once(stack, app):
    before = balance(app)
    r = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500)
    assert r.status_code == 201, r.text
    started = r.json()
    action = started["action"]
    assert action["checks"] == ["one-time code", "typing check"]
    assert action["reason"] == "We need a quick extra check because this payee has not been paid before."
    assert not {"riskScore", "threatLevel", "amfaTxn"} & set(action)   # nothing for a fraudster to learn

    early = app.post(f"/api/actions/{action['id']}/complete").json()
    assert early["action"]["status"] == "AWAITING_VERIFICATION" and balance(app) == before

    verify(stack, started["challengeUrl"])
    assert "₹2,500.00" in SENT[-1] and "V. Mehta" in SENT[-1]   # what-you-see-is-what-you-sign
    done = app.post(f"/api/actions/{action['id']}/complete").json()
    assert done["action"]["status"] == "DONE" and done["balance"] == before - 2500_00
    again = app.post(f"/api/actions/{action['id']}/complete").json()
    assert again["balance"] == before - 2500_00                   # applied once


def test_proof_is_bound_to_the_amount(stack, app):
    """If anything changes the action after approval, the proof no longer matches and nothing moves."""
    before = balance(app)
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=100).json()
    verify(stack, started["challengeUrl"])
    _, record = stack.bank.ACTIONS[started["action"]["id"]]
    record["amountMinor"] = 90_000_00
    out = app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]
    assert out["status"] == "FAILED" and "BINDING_MISMATCH" in out["detail"]
    assert balance(app) == before


def test_a_new_payee_is_usable_only_after_approval(stack, app):
    started = act(app, "payee", name="Ravi Kumar", account="123456789012", ifsc="HDFC0001234").json()
    assert act(app, "transfer", payeeRef=_ref("HDFC0001234", "123456789012"), amountRupees=10).status_code == 404
    verify(stack, started["challengeUrl"])
    app.post(f"/api/actions/{started['action']['id']}/complete")
    payees = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["payees"]
    assert "Ravi Kumar ••9012" in [p["display"] for p in payees]


def _ref(ifsc, account):
    import hashlib
    return "BEN-" + hashlib.sha256(f"{ifsc}:{account}".encode()).hexdigest()[:8].upper()


def test_an_unknown_device_must_be_verified_first(stack, app):
    laptop = "device-laptop-0002"
    r = act(app, "transfer", device=laptop, payeeRef="BEN-42", amountRupees=10)
    assert r.status_code == 403
    started = act(app, "device", device=laptop).json()
    verify(stack, started["challengeUrl"])
    assert app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]["status"] == "DONE"
    assert act(app, "transfer", device=laptop, payeeRef="BEN-42", amountRupees=10).status_code == 201


def test_every_action_kind_is_approved_before_it_applies(stack, app):
    for kind, body in [("bill", {"billerRef": "BILL-ELEC", "amountRupees": 1200}),
                       ("limit", {"limitRupees": 300000}),
                       ("contact", {"field": "email", "value": "asha@example.com"}),
                       ("password", {"current": "long-password-1", "new": "another-password-2"})]:
        started = act(app, kind, **body)
        assert started.status_code == 201, (kind, started.text)
        verify(stack, started.json()["challengeUrl"])
        out = app.post(f"/api/actions/{started.json()['action']['id']}/complete").json()["action"]
        assert out["status"] == "DONE", (kind, out)
    c = stack.bank.CUSTOMERS[app.user]
    assert c["dailyLimit"] == 300000_00 and c["email"] == "asha@example.com"
    assert c["events"]["contactChangedAt"] and c["events"]["passwordResetAt"]


def test_account_changes_reach_the_risk_model(stack, app):
    app.post("/api/demo/sim-swap").raise_for_status()
    app.post("/api/demo/payee-risk", json={"payeeRef": "BEN-42", "flagged": True}).raise_for_status()
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()
    with db.session() as s:
        txn_id = _txn(s, started["action"]["id"]).id
        features = s.execute(select(db.Decision).where(db.Decision.transaction_id == txn_id)).scalar_one() \
            .context["_features"]
    assert features["sim_changed_hours"] is not None and features["sim_changed_hours"] < 1
    assert features["payee_risk_flag"] == 1


def _txn(s, external_id):
    return s.execute(select(db.Transaction).where(db.Transaction.external_id == external_id)).scalar_one()


def test_risky_payments_warn_about_scams(stack, app):
    LEVEL["now"] = "T2"
    action = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()["action"]
    assert action["scamWarning"] and "face check" in action["checks"]
    assert not act(app, "contact", field="phone", value="+919800000000").json()["action"]["scamWarning"]


def held_after_checks(stack, app):
    """A T4 request: the customer still does the checks they can, then it waits for a person."""
    LEVEL["now"] = "T4"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()
    assert started["action"]["status"] == "AWAITING_VERIFICATION" and started["challengeUrl"]
    assert started["action"]["reviewAfter"]
    verify(stack, started["challengeUrl"])
    out = app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]
    assert out["status"] == "UNDER_REVIEW"
    return started["action"]


def test_severe_risk_waits_for_a_person_after_the_checks_and_can_be_cancelled(stack, app):
    action = held_after_checks(stack, app)
    out = app.post(f"/api/actions/{action['id']}/cancel").json()["action"]
    assert out["status"] == "CANCELLED"


def test_a_held_request_can_be_approved_by_two_reviewers(stack, app):
    """Before, a request held for review never had a customer code in it, so it could only be rejected."""
    before = balance(app)
    action = held_after_checks(stack, app)
    with db.session() as s:
        txn = _txn(s, action["id"])
        case = s.execute(select(db.ReviewCase).where(db.ReviewCase.transaction_id == txn.id)).scalar_one()
        assert "T4" in case.reason
        first = orchestrator.review_decision(s, case, approve=True, operator=SimpleNamespace(id="op_a"), note="called her back")
        assert first["status"] == "AWAITING_SECOND_APPROVAL"
        assert orchestrator.review_decision(s, case, approve=True, operator=SimpleNamespace(id="op_b"),
                                            note="confirmed by phone")["proofIssued"]
    done = app.post(f"/api/actions/{action['id']}/complete").json()
    assert done["action"]["status"] == "DONE" and "Bank review" in done["action"]["confirmedWith"]
    assert done["balance"] == before - LARGE * 100


def _face_results(monkeypatch, statuses):
    """FACE answers with `statuses` in turn (then keeps the last one); every other check passes."""
    seq = iter(statuses)
    last = {"s": statuses[-1]}

    def verify_(factor, **kw):
        if factor != "FACE":
            return FactorResult(factor=factor, status="PASSED", score=0.9, threshold=0.5)
        last["s"] = next(seq, last["s"])
        reason = "REPLAY_DUPLICATE_FRAMES" if last["s"] == "SPOOF_DETECTED" else None
        return FactorResult(factor=factor, status=last["s"], score=0.9, threshold=0.5, failureReason=reason)
    monkeypatch.setattr(factors, "verify", verify_)


def _customer_status(stack, app):
    with db.session() as s:
        return s.get(db.Customer, stack.bank.CUSTOMERS[app.user]["amfaUser"]).status


def test_a_face_flagged_as_an_attack_twice_can_still_pass_on_the_third_try(stack, app, monkeypatch):
    _face_results(monkeypatch, ["SPOOF_DETECTED", "SPOOF_DETECTED", "PASSED"])
    LEVEL["now"] = "T2"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()
    assert "face check" in started["action"]["checks"]
    verify(stack, started["challengeUrl"])
    done = app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]
    assert done["status"] == "DONE"
    assert _customer_status(stack, app) == "ACTIVE"


def test_the_third_attack_flag_locks_the_customer(stack, app, monkeypatch):
    _face_results(monkeypatch, ["SPOOF_DETECTED"])
    LEVEL["now"] = "T2"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()
    verify(stack, started["challengeUrl"])
    out = app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]
    assert out["status"] == "DECLINED"
    assert _customer_status(stack, app) == "LOCKED"
    with db.session() as s:
        txn = _txn(s, started["action"]["id"])
        spoofs = s.execute(select(db.Attempt).where(db.Attempt.session_id == txn.session_id,
                                                    db.Attempt.status == "SPOOF_DETECTED")).scalars().all()
        assert len(spoofs) == 3


def test_a_rejected_review_is_declined_and_labelled(stack, app):
    action = held_after_checks(stack, app)
    with db.session() as s:
        txn = _txn(s, action["id"])
        case = s.execute(select(db.ReviewCase).where(db.ReviewCase.transaction_id == txn.id)).scalar_one()
        orchestrator.review_decision(s, case, approve=False, operator=SimpleNamespace(id="op_test"), note="not her")
    listed = app.get("/api/activity").json()["activity"]
    assert next(a for a in listed if a["id"] == action["id"])["status"] == "DECLINED"
    with db.session() as s:
        label = s.execute(select(db.RiskLabel).where(db.RiskLabel.transaction_id == txn.id)).scalar_one()
        assert (label.label, label.source) == ("FRAUD", "review_verdict")


def test_this_was_not_me_becomes_a_fraud_label(stack, app):
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()
    aid = started["action"]["id"]
    assert app.post(f"/api/actions/{aid}/report", json={}).status_code == 409   # not completed yet
    verify(stack, started["challengeUrl"])
    app.post(f"/api/actions/{aid}/complete")
    assert app.post(f"/api/actions/{aid}/report", json={"reason": "I never sent this"}).json()["action"]["reported"]
    with db.session() as s:
        txn = _txn(s, aid)
        label = s.execute(select(db.RiskLabel).where(db.RiskLabel.transaction_id == txn.id)).scalar_one()
        assert (label.label, label.source, label.reason) == ("FRAUD", "customer_report", "I never sent this")
        assert s.execute(select(db.SecurityEvent).where(db.SecurityEvent.session_id == txn.session_id,
                                                        db.SecurityEvent.kind == "FRAUD_REPORTED")).scalar_one()


def test_customers_cannot_touch_each_others_actions(stack, app):
    aid = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()["action"]["id"]
    other = TestClient(stack.bank.app)
    other.post("/api/register", json={"username": f"o{uuid.uuid4().hex[:8]}", "name": "Other", "phone": "+919800000001",
                                      "password": "long-password-1", "deviceId": "device-other-0003"})
    for path in ("complete", "cancel", "report", "continue"):
        assert other.post(f"/api/actions/{aid}/{path}", json={}).status_code == 404


def test_the_app_is_served_as_modules_and_nothing_outside_its_assets(stack):
    """The bank's files live under /assets, so the bank and AMFA (whose files are under /static) can share
    one address."""
    client = TestClient(stack.bank.app)
    for path in ("/assets/js/app.js", "/assets/js/screens/welcome.js"):
        r = client.get(path)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], path
    assert client.get("/assets/config.json").json()["amfaBase"]
    page = client.get("/").text
    assert "/assets/app.css" in page and "/assets/js/app.js" in page and "/static/" not in page
    for path in ("/assets/%2e%2e/bank.py", "/assets/js/%2e%2e/%2e%2e/bank.py", "/assets/js/nope.js", "/static/js/app.js"):
        assert client.get(path).status_code == 404, path


def _token(url):
    return url.split("#token=", 1)[1]


def test_enrol_asks_only_for_what_is_requested_and_in_the_banks_colours(stack, app):
    url = app.post("/api/amfa/enrol", json={"factors": ["VOICE"]}).json()["url"]
    assert "embed=1" in url.split("#", 1)[0]
    st = stack.core.get("/c/v1/enrollment", headers={"authorization": f"Bearer {_token(url)}"}).json()
    assert st["requested"] == ["VOICE"]
    assert st["theme"]["bank"] == "Demo Bank" and st["theme"]["color"].startswith("#")


def test_the_verification_page_gets_the_theme_too(stack, app):
    url = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()["challengeUrl"]
    assert "embed=1" in url.split("#", 1)[0]
    st = stack.core.get("/c/v1/session", headers={"authorization": f"Bearer {_token(url)}"}).json()
    assert st["theme"]["bank"] == "Demo Bank"


def test_protection_level_counts_the_ways_to_confirm(stack, app):
    p = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["protection"]
    assert p["level"] == "Strong"
    assert set(p["have"]) == {"SMS code", "Face", "Voice", "Typing"}
    assert p["canAdd"] == ["Authenticator app"]


def test_a_closed_verification_can_be_continued(stack, app):
    aid = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()["action"]["id"]
    listed = next(a for a in app.get("/api/activity").json()["activity"] if a["id"] == aid)
    assert listed["canContinue"]
    url = app.post(f"/api/actions/{aid}/continue").json()["challengeUrl"]
    verify(stack, url)
    done = app.post(f"/api/actions/{aid}/complete").json()["action"]
    assert done["status"] == "DONE" and done["confirmedWith"] == ["SMS code", "Typing"]
    assert done["payee"] == {"display": "V. Mehta ••4411", "initials": "VM"}
    assert app.post(f"/api/actions/{aid}/continue").status_code == 409


def test_recent_payees_are_the_ones_paid_most_recently(stack, app):
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()
    verify(stack, started["challengeUrl"])
    app.post(f"/api/actions/{started['action']['id']}/complete")
    recent = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["recentPayees"]
    assert recent == [{"ref": "BEN-42", "display": "V. Mehta ••4411", "initials": "VM"}]


def test_a_frozen_account_cannot_start_anything(stack, app):
    app.post("/api/account/freeze").raise_for_status()
    with db.session() as s:
        assert s.get(db.Customer, stack.bank.CUSTOMERS[app.user]["amfaUser"]).status == "SUSPENDED"
    for kind, body in [("transfer", {"payeeRef": "BEN-42", "amountRupees": 10}),
                       ("bill", {"billerRef": "BILL-ELEC", "amountRupees": 10}),
                       ("payee", {"name": "Ravi Kumar", "account": "123456789012", "ifsc": "HDFC0001234"}),
                       ("limit", {"limitRupees": 5000}), ("contact", {"field": "email", "value": "a@b.co"}),
                       ("password", {"current": "long-password-1", "new": "another-password-2"}),
                       ("device", {})]:
        assert act(app, kind, **body).status_code == 423, kind
    assert app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["frozen"]
    app.post("/api/demo/unfreeze").raise_for_status()
    assert act(app, "transfer", payeeRef="BEN-42", amountRupees=10).status_code == 201


def test_the_decision_is_for_the_presenter_only(stack, app, monkeypatch):
    LEVEL["now"] = "T2"
    action = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()["action"]
    assert "decision" not in action
    for a in app.get("/api/activity").json()["activity"]:
        assert "decision" not in a and "challengeUrl" not in a
    d = app.get("/api/demo/last-decision").json()["decision"]
    assert d["threatLevel"] == "T2" and d["checks"] and d["reasons"][0] == {
        "feature": "payee_seen", "text": "payee not paid before", "weight": 0.2}
    monkeypatch.setattr(stack.bank, "DEMO_TOOLS", False)
    assert app.get("/api/demo/last-decision").status_code == 404


def test_bad_amounts_never_start_anything(stack, app):
    for amount in (0, -5, 0.001, 10 ** 9):
        assert act(app, "transfer", payeeRef="BEN-42", amountRupees=amount).status_code == 422, amount
    over_limit = act(app, "transfer", payeeRef="BEN-42", amountRupees=2_00_001)
    assert over_limit.status_code == 422 and "daily limit" in over_limit.json()["detail"]
