"""End to end: the Demo Bank customer app running against a real in-process core. Each test drives the
bank's API like the phone app would, completes the checks AMFA asks for through the client API, and checks
the bank only acts on a verified, consumed proof bound to that exact action. The risk model is a stub whose
threat level each test sets; the biometric services are scripted; OTP codes are read from the delivery."""
import importlib
import os
import re
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from amfa import config
from amfa.common import crypto
from amfa.common.contract import FactorResult
from amfa.core import api_admin, db, factors, orchestrator, otp
from amfa.core.app import create_app
from amfa.core.auth import DEFAULT_SCOPES
from amfa.engine.risk import scorer

PHONE_DEVICE = "device-phone-0001"
LARGE = 20_000   # rupees: above the bank's LOW band, so the small-payment cap keeps the model's level
LEVEL = {"now": "T1"}
SENT: list[str] = []
SENT_TO: list[dict] = []
EMAIL = "asha.rao@example.com"


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
    monkeypatch.setattr(otp, "deliver", lambda contact, message: SENT.append(message) or SENT_TO.append(contact) or "email")


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """One test client is one IP, and the module's tests together make more requests a minute than the per-IP limit."""
    from amfa.common import kv
    store = kv.get()
    for key in [k for k in getattr(store, "_data", {}) if k.startswith("rl:")]:
        store._data.pop(key, None)


@pytest.fixture
def app(stack):
    """A signed-in, enrolled customer on their own trusted phone."""
    client = TestClient(stack.bank.app)
    user = f"u{uuid.uuid4().hex[:8]}"
    r = client.post("/api/register", json={"username": user, "name": "Asha Rao", "password": "long-password-1",
                                           "phone": "+919812345678", "email": EMAIL, "deviceId": PHONE_DEVICE})
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
        assert "challenge" in state, state
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


def test_adding_a_payee_never_waits_for_a_person(stack, app):
    """Saving a payee moves no money (the first payment to it is checked on its own), so even at the
    highest risk the customer does every check they can and it applies straight away."""
    LEVEL["now"] = "T4"
    started = act(app, "payee", name="Ravi Kumar", account="123456789099", ifsc="HDFC0001234").json()
    assert not started["action"]["reviewAfter"] and started["challengeUrl"]
    verify(stack, started["challengeUrl"])
    out = app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]
    assert out["status"] == "DONE"
    list_payee_as_risky(stack, app)
    held = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()["action"]
    assert held["reviewAfter"]                              # money to a risk-listed payee still waits for a person


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


def list_payee_as_risky(stack, app, ref="BEN-42"):
    stack.bank.CUSTOMERS[app.user]["payees"][ref]["riskFlag"] = True


def held_after_checks(stack, app):
    """A payee on a risk list: the customer still does the checks they can, then it waits for a person."""
    LEVEL["now"] = "T4"
    list_payee_as_risky(stack, app)
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()
    assert started["action"]["status"] == "AWAITING_VERIFICATION" and started["challengeUrl"]
    assert started["action"]["reviewAfter"]
    verify(stack, started["challengeUrl"])
    out = app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]
    assert out["status"] == "UNDER_REVIEW"
    return started["action"]


def test_the_highest_risk_level_alone_is_handled_without_a_person(stack, app):
    """T4 used to mean a queue. Now it means every check the customer has, then the payment goes through."""
    before = balance(app)
    LEVEL["now"] = "T4"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()
    assert not started["action"]["reviewAfter"] and "face check" in started["action"]["checks"]
    verify(stack, started["challengeUrl"])
    done = app.post(f"/api/actions/{started['action']['id']}/complete").json()
    assert done["action"]["status"] == "DONE" and done["balance"] == before - LARGE * 100


def test_a_large_first_payment_carries_the_scam_warning_even_at_a_low_level(stack, app):
    LEVEL["now"] = "T1"
    first = act(app, "payee", name="Croma Electronics", account="123456780001", ifsc="HDFC0001234").json()
    verify(stack, first["challengeUrl"])
    app.post(f"/api/actions/{first['action']['id']}/complete").raise_for_status()
    store = _ref("HDFC0001234", "123456780001")
    tv = act(app, "transfer", payeeRef=store, amountRupees=150_000).json()["action"]   # a TV: large, first payment
    assert tv["scamWarning"] and not tv["reviewAfter"]
    assert not act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()["action"]["scamWarning"]


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
        assert "risk list" in case.reason
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
                                      "email": "other@example.com", "password": "long-password-1",
                                      "deviceId": "device-other-0003"})
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
    assert set(p["have"]) == {"Email code", "Face", "Voice", "Typing"}
    assert p["canAdd"] == ["Authenticator app"]


def test_a_closed_verification_can_be_continued(stack, app):
    aid = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()["action"]["id"]
    listed = next(a for a in app.get("/api/activity").json()["activity"] if a["id"] == aid)
    assert listed["canContinue"]
    url = app.post(f"/api/actions/{aid}/continue").json()["challengeUrl"]
    verify(stack, url)
    done = app.post(f"/api/actions/{aid}/complete").json()["action"]
    assert done["status"] == "DONE" and done["confirmedWith"] == ["Email code", "Typing"]
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


def test_one_time_codes_go_to_the_customers_email(stack, app):
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()
    verify(stack, started["challengeUrl"])
    assert SENT_TO[-1] == {"channel": "email", "to": EMAIL}
    me = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()
    assert me["codeTo"] == "a***@example.com" and me["email"] == "a•••@example.com"


def test_changing_the_email_moves_the_codes_to_the_new_address(stack, app):
    started = act(app, "contact", field="email", value="asha.new@example.com").json()
    verify(stack, started["challengeUrl"])
    assert SENT_TO[-1]["to"] == EMAIL                    # the change itself is confirmed at the old address
    app.post(f"/api/actions/{started['action']['id']}/complete").raise_for_status()
    nxt = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()
    verify(stack, nxt["challengeUrl"])
    assert SENT_TO[-1] == {"channel": "email", "to": "asha.new@example.com"}


def test_sign_up_needs_a_real_email(stack):
    client = TestClient(stack.bank.app)
    for bad in ("", "not-an-email", "a@b"):
        r = client.post("/api/register", json={"username": f"x{uuid.uuid4().hex[:8]}", "name": "Asha Rao", "email": bad,
                                               "password": "long-password-1", "phone": "+919812345678",
                                               "deviceId": "device-x-000001"})
        assert r.status_code == 422, bad


def code_session(stack, started):
    auth = {"authorization": f"Bearer {started['challengeUrl'].split('#token=', 1)[1]}"}
    ch = stack.core.get("/c/v1/session", headers=auth).json()["challenge"]
    assert ch["factor"] == "OTP"
    return auth, ch


def test_a_code_can_be_resent_and_only_the_newest_one_works(stack, app, monkeypatch):
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500).json()
    auth, first = code_session(stack, started)
    old_code = re.match(r"(\d+)", SENT[-1]).group(1)
    assert first["expiresAt"] >= int(time.time()) + otp.DELIVERED_TTL_S - 5   # the timer matches the code's life
    too_soon = stack.core.post("/c/v1/session/otp/resend", headers=auth)
    assert too_soon.status_code == 429 and too_soon.json()["error"]["code"] == "OTP_RESEND_WAIT"

    monkeypatch.setattr(orchestrator, "OTP_RESEND_WAIT_S", 0)
    sends = len(SENT)
    again = stack.core.post("/c/v1/session/otp/resend", headers=auth)
    assert again.status_code == 200, again.text
    assert len(SENT) == sends + 1 and again.json()["challenge"]["challengeId"] != first["challengeId"]
    stale = stack.core.post("/c/v1/session/factors/OTP", headers=auth,
                            json={"challengeId": first["challengeId"], "payload": {"code": old_code}})
    assert stale.status_code == 409                       # the old code's screen is closed
    assert verify(stack, started["challengeUrl"])["status"] == "VERIFIED"


def test_a_wrong_code_can_be_retried_with_the_same_code(stack, app, monkeypatch):
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500).json()
    auth, ch = code_session(stack, started)
    code, sends = re.match(r"(\d+)", SENT[-1]).group(1), len(SENT)
    wrong = "000000" if code != "000000" else "111111"
    out = stack.core.post("/c/v1/session/factors/OTP", headers=auth,
                          json={"challengeId": ch["challengeId"], "payload": {"code": wrong}}).json()
    assert out["result"]["retry"] and out["result"]["guidance"] == "That code is not valid. Check it and try again."
    retry = out["state"]["challenge"]
    assert retry["factor"] == "OTP" and len(SENT) == sends            # no new email: the same code still works
    assert retry["expiresAt"] == ch["expiresAt"]                       # and the timer carries on
    ok = stack.core.post("/c/v1/session/factors/OTP", headers=auth,
                         json={"challengeId": retry["challengeId"], "payload": {"code": code}}).json()
    assert ok["result"]["status"] == "PASSED"


def test_a_resent_code_replaces_the_old_one(stack, app, monkeypatch):
    monkeypatch.setattr(orchestrator, "OTP_RESEND_WAIT_S", 0)
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500).json()
    auth, _ = code_session(stack, started)
    old = re.match(r"(\d+)", SENT[-1]).group(1)
    ch = stack.core.post("/c/v1/session/otp/resend", headers=auth).json()["challenge"]
    if re.match(r"(\d+)", SENT[-1]).group(1) == old:
        return                                                         # one in a million: the same digits again
    out = stack.core.post("/c/v1/session/factors/OTP", headers=auth,
                          json={"challengeId": ch["challengeId"], "payload": {"code": old}}).json()
    assert out["result"]["status"] == "PENDING" and out["result"]["retry"]


def test_codes_stop_after_the_send_limit(stack, app, monkeypatch):
    monkeypatch.setattr(orchestrator, "OTP_RESEND_WAIT_S", 0)
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500).json()
    auth, _ = code_session(stack, started)
    for _ in range(orchestrator.MAX_OTP_SENDS - 1):
        stack.core.post("/c/v1/session/otp/resend", headers=auth).raise_for_status()
    over = stack.core.post("/c/v1/session/otp/resend", headers=auth)
    assert over.status_code == 429 and over.json()["error"]["code"] == "OTP_SEND_LIMIT"
    assert verify(stack, started["challengeUrl"])["status"] == "VERIFIED"   # the last code still works


def _session_steps(stack, challenge_url):
    token = challenge_url.split("#token=", 1)[1]
    auth = {"authorization": f"Bearer {token}"}
    return auth, stack.core.get("/c/v1/session", headers=auth).json()


def test_typing_on_a_phone_needs_phone_templates(stack, app, monkeypatch):
    """Computer templates cannot check thumb typing: on a phone without phone templates the typing
    check is replaced; once the phone is enrolled it is used, against the phone set."""
    LEVEL["now"] = "T1"
    monkeypatch.setattr(factors, "ueba_phrase", lambda: {"phrase": "the quick brown fox 2026",
                                                         "shape": [[0.15, 0.8], [0.85, 0.8]]})
    r = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500, touch=True)
    assert r.status_code == 201, r.text
    assert "typing check" not in r.json()["action"]["checks"]

    amfa_user = stack.bank.CUSTOMERS[app.user]["amfaUser"]
    with db.session() as s:
        row = s.execute(select(db.Enrollment).where(db.Enrollment.customer_id == amfa_user,
                                                    db.Enrollment.factor == "UEBA")).scalars().one()
        row.detail = {"inputs": ["keyboard", "touch"], "shapeInputs": ["keyboard", "touch"], "shape": True}
    r = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500, touch=True)
    started = r.json()
    assert "typing check" in started["action"]["checks"]
    auth, st = _session_steps(stack, started["challengeUrl"])
    for _ in range(4):
        ch = st["challenge"]
        if ch["factor"] == "UEBA":
            break
        payload = {"code": re.match(r"(\d+)", SENT[-1]).group(1)} if ch["factor"] == "OTP" else {}
        st = stack.core.post(f"/c/v1/session/factors/{ch['factor']}", headers=auth,
                             json={"challengeId": ch["challengeId"], "payload": payload}).json()["state"]
    assert ch["factor"] == "UEBA"
    assert ch["params"]["input"] == "touch" and ch["params"]["shape"]


def test_a_computer_still_gets_the_computer_challenge(stack, app):
    LEVEL["now"] = "T1"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500).json()
    assert "typing check" in started["action"]["checks"]


def _jwk():
    from cryptography.hazmat.primitives.asymmetric import ec
    n = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    b = lambda v: crypto.b64e(v.to_bytes(32, "big"))
    return {"kty": "EC", "crv": "P-256", "x": b(n.x), "y": b(n.y)}


def _passed_session(stack, app, level):
    LEVEL["now"] = level
    # above the small-payment line, so the model's level (and its face check) stands
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()
    state = verify(stack, started["challengeUrl"])
    token = started["challengeUrl"].split("#token=", 1)[1]
    return {"authorization": f"Bearer {token}"}, state


def _add_key(stack, auth, device=PHONE_DEVICE, jwk=None):
    return stack.core.post("/c/v1/session/device-key", headers=auth,
                           json={"deviceId": device, "publicJwk": jwk or _jwk()})


def test_a_device_adds_its_own_key_after_an_approval_with_a_face_check(stack, app):
    auth, state = _passed_session(stack, app, "T2")          # code, typing and face
    assert state["status"] == "VERIFIED"
    jwk = _jwk()
    r = _add_key(stack, auth, jwk=jwk)
    assert r.status_code == 200 and r.json() == {"bound": True, "added": True}
    assert _add_key(stack, auth, jwk=jwk).json() == {"bound": True, "added": False}   # the same key again
    assert _add_key(stack, auth).status_code == 409          # never swapped for a different key


def test_no_key_without_a_face_or_voice_check(stack, app):
    auth, state = _passed_session(stack, app, "T1")          # code and typing only
    assert state["status"] == "VERIFIED"
    r = _add_key(stack, auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "LIVE_CHECK_REQUIRED"


def test_only_the_device_that_was_approved_can_add_a_key(stack, app):
    auth, _ = _passed_session(stack, app, "T2")
    r = _add_key(stack, auth, device="another-device-00099")
    assert r.status_code == 403 and r.json()["error"]["code"] == "WRONG_DEVICE"


def test_no_key_before_the_approval_has_passed(stack, app):
    LEVEL["now"] = "T2"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=2500).json()
    auth = {"authorization": "Bearer " + started["challengeUrl"].split("#token=", 1)[1]}
    r = _add_key(stack, auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SESSION_NOT_VERIFIED"


# ---- codes handed to the bank to deliver (AMFA_OTP_PROVIDER=webhook) and shown on a demo server

def signed_otp(secret, to, text, ts=None, tamper=False):
    import base64
    import hashlib
    import hmac
    import json
    raw = json.dumps({"id": "otp_x", "channel": "email", "to": to, "messageB64": base64.urlsafe_b64encode(text.encode()).decode().rstrip("="),
                      "ts": int(ts or time.time())}, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"x-amfa-signature": ("0" * 64 if tamper else sig), "content-type": "application/json"}


def test_a_code_from_amfa_reaches_only_the_customer_it_was_for(stack, app, monkeypatch):
    monkeypatch.setattr(stack.bank, "OTP_WEBHOOK_SECRET", "s" * 40)
    other = TestClient(stack.bank.app)
    other.post("/api/register", json={"username": f"o{uuid.uuid4().hex[:8]}", "name": "Other Person", "password": "long-password-1",
                                      "phone": "+919800000001", "email": "other@example.com", "deviceId": "device-other-0001"})
    assert app.get("/api/demo/code").json() == {"code": None}
    raw, headers = signed_otp("s" * 40, EMAIL, "482913 is your Demo Bank code to approve ₹2,500.00 to V. Mehta. Valid 3 minutes.")
    assert app.post("/webhooks/otp", content=raw, headers=headers).json() == {"received": True}
    shown = app.get("/api/demo/code").json()
    assert shown["code"] == "482913" and shown["secondsLeft"] > 100
    assert other.get("/api/demo/code").json() == {"code": None}          # someone else's code is not theirs to see


def test_a_forged_or_stale_code_message_is_refused(stack, app, monkeypatch):
    monkeypatch.setattr(stack.bank, "OTP_WEBHOOK_SECRET", "s" * 40)
    raw, headers = signed_otp("s" * 40, EMAIL, "111111 is your code", tamper=True)
    assert app.post("/webhooks/otp", content=raw, headers=headers).status_code == 401
    raw, headers = signed_otp("s" * 40, EMAIL, "111111 is your code", ts=time.time() - 3600)
    assert app.post("/webhooks/otp", content=raw, headers=headers).status_code == 400
    raw, headers = signed_otp("wrong-secret-" + "x" * 30, EMAIL, "111111 is your code")
    assert app.post("/webhooks/otp", content=raw, headers=headers).status_code == 401
    monkeypatch.setattr(stack.bank, "OTP_WEBHOOK_SECRET", "")
    raw, headers = signed_otp("", EMAIL, "111111 is your code")
    assert app.post("/webhooks/otp", content=raw, headers=headers).status_code == 401    # no secret configured: nothing is accepted
    assert app.get("/api/demo/code").json() == {"code": None}


def test_the_code_is_not_shown_when_demo_tools_are_off(stack, app, monkeypatch):
    monkeypatch.setattr(stack.bank, "DEMO_TOOLS", False)
    assert app.get("/api/demo/code").status_code == 404


# ---- end-to-end encryption of what the phone sends ---------------------------------------------------------------

def _set_require_e2e(stack, on):
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        t.policy = {**(t.policy or {}), "requireE2E": on}


def _face_session(stack, app):
    LEVEL["now"] = "T2"
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=LARGE).json()
    token = started["challengeUrl"].split("#token=", 1)[1]
    auth = {"authorization": f"Bearer {token}"}
    state = stack.core.get("/c/v1/session", headers=auth).json()
    return auth, state


def _pass_other_checks(stack, auth):
    for _ in range(4):
        ch = stack.core.get("/c/v1/session", headers=auth).json()["challenge"]
        if ch is None or ch["factor"] == "FACE":
            return ch
        payload = {"code": re.match(r"(\d+)", SENT[-1]).group(1)} if ch["factor"] == "OTP" else {}
        stack.core.post(f"/c/v1/session/factors/{ch['factor']}", headers=auth,
                        json={"challengeId": ch["challengeId"], "payload": payload}).raise_for_status()


def test_an_encrypted_face_submission_is_opened_by_the_core_and_only_there(stack, app, monkeypatch):
    from amfa.common import e2e
    seen = {}

    def verify_(factor, **kw):
        seen[factor] = kw
        return FactorResult(factor=factor, status="PASSED", score=0.9, threshold=0.5)
    monkeypatch.setattr(factors, "verify", verify_)
    auth, state = _face_session(stack, app)
    ch = _pass_other_checks(stack, auth)
    assert ch and ch["factor"] == "FACE"
    pub = stack.core.get("/c/v1/e2e-key").json()
    assert pub["alg"] == e2e.ALG and pub["kid"] == e2e.public_key()["kid"]
    frames = ["data:image/jpeg;base64,/9j/AAAA"]
    sid = state["sessionId"]
    box = e2e.encrypt(pub, {"frames": frames}, f"{sid}|FACE|{ch['challengeId']}")
    assert "/9j/AAAA" not in str(box)
    r = stack.core.post("/c/v1/session/factors/FACE", headers=auth,
                        json={"challengeId": ch["challengeId"], "payload": {"e2e": box}})
    assert r.status_code == 200, r.text
    assert "FACE" in seen                                         # the biometric service got the decrypted pictures


def test_a_box_for_another_challenge_or_a_changed_box_is_refused(stack, app, monkeypatch):
    from amfa.common import e2e
    auth, state = _face_session(stack, app)
    ch = _pass_other_checks(stack, auth)
    pub = stack.core.get("/c/v1/e2e-key").json()
    sid = state["sessionId"]
    wrong = e2e.encrypt(pub, {"frames": []}, f"{sid}|FACE|ch_somebody_elses")
    r = stack.core.post("/c/v1/session/factors/FACE", headers=auth,
                        json={"challengeId": ch["challengeId"], "payload": {"e2e": wrong}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "E2E_INVALID"


def test_a_bank_can_require_encryption_and_plain_biometrics_are_then_refused(stack, app):
    _set_require_e2e(stack, True)
    try:
        auth, _ = _face_session(stack, app)
        for _ in range(4):                                         # codes stay plain; the first biometric must be a box
            ch = stack.core.get("/c/v1/session", headers=auth).json()["challenge"]
            payload = {"code": re.match(r"(\d+)", SENT[-1]).group(1)} if ch["factor"] == "OTP" else {}
            r = stack.core.post(f"/c/v1/session/factors/{ch['factor']}", headers=auth,
                                json={"challengeId": ch["challengeId"], "payload": payload})
            if ch["factor"] != "OTP":
                assert r.status_code == 400 and r.json()["error"]["code"] == "E2E_REQUIRED"
                return
            assert r.status_code == 200
        raise AssertionError("no biometric check was asked for")
    finally:
        _set_require_e2e(stack, False)


# ---- the customer's own "no extra checks up to X" limit ----------------------------------------------------------------

def _bypass_policy(stack, **cb):
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        t.policy = {**(t.policy or {}), "customerBypass": cb}


@pytest.fixture
def bypass_on(stack):
    _bypass_policy(stack, enabled=True, maxMinor=10_000_00, coolingOffHours=0)
    yield
    _bypass_policy(stack, enabled=False)


def _set_nocheck(stack, app, rupees):
    started = act(app, "nocheck", limitRupees=rupees)
    assert started.status_code == 201, started.text
    verify(stack, started.json()["challengeUrl"])
    return app.post(f"/api/actions/{started.json()['action']['id']}/complete").json()["action"], started.json()["action"]["id"]


def _in_force(stack, app):
    from amfa.core import bypass
    from amfa.engine.policy import TenantPolicy
    with db.session() as s:
        cust = s.get(db.Customer, stack.bank.CUSTOMERS[app.user]["amfaUser"])
        pol = TenantPolicy.model_validate(s.get(db.Tenant, stack.tenant["id"]).policy)
        return bypass.limit_for(s, cust.id, pol, "INR", time.time())


def test_a_limit_is_set_only_by_passing_the_checks_and_then_applies(stack, app, bypass_on):
    LEVEL["now"] = "T2"
    assert _in_force(stack, app) is None
    done, _ = _set_nocheck(stack, app, 3000)
    assert done["status"] == "DONE", done["detail"]
    assert _in_force(stack, app) == 3000_00
    me = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()
    assert me["noCheck"]["limit"] == 3000_00 and me["noCheck"]["pending"] is None


def test_a_raise_waits_out_the_cooling_off_but_a_cut_is_immediate(stack, app, bypass_on):
    _bypass_policy(stack, enabled=True, maxMinor=10_000_00, coolingOffHours=0)
    _set_nocheck(stack, app, 2000)
    _bypass_policy(stack, enabled=True, maxMinor=10_000_00, coolingOffHours=24)
    raised, _ = _set_nocheck(stack, app, 8000)
    me = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["noCheck"]
    assert raised["status"] == "DONE" and me["limit"] == 2000_00 and me["pending"] == 8000_00
    assert me["pendingFrom"] - time.time() > 23 * 3600 and _in_force(stack, app) == 2000_00   # still the old limit
    _set_nocheck(stack, app, 1000)
    assert _in_force(stack, app) == 1000_00                          # lowering never waits (and drops the pending raise)
    assert app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["noCheck"]["pending"] is None


def test_a_raise_counts_once_its_cooling_off_has_passed(stack, app, bypass_on):
    _bypass_policy(stack, enabled=True, maxMinor=10_000_00, coolingOffHours=24)
    _set_nocheck(stack, app, 5000)
    assert _in_force(stack, app) is None
    with db.session() as s:
        row = s.get(db.BypassLimit, stack.bank.CUSTOMERS[app.user]["amfaUser"])
        row.pending_from = time.time() - 1
    assert _in_force(stack, app) == 5000_00


def test_the_limit_cannot_be_set_without_its_own_verified_change_limit_transaction(stack, app, bypass_on):
    AmfaError = stack.bank.AmfaError
    amfa_user = stack.bank.CUSTOMERS[app.user]["amfaUser"]
    done, action_id = _set_nocheck(stack, app, 2000)
    with db.session() as s:
        used = _txn(s, action_id).id
    put = lambda body: stack.bank.AMFA.request("PUT", f"/v1/users/{amfa_user}/bypass-limit", body)
    with pytest.raises(AmfaError) as e:                              # the same approval cannot be used twice
        put({"amountMinor": 2000_00, "transactionId": used})
    assert e.value.code == "BYPASS_NOT_AUTHORISED"
    other = act(app, "limit", limitRupees=200_000).json()            # an approved limit change for another amount
    verify(stack, other["challengeUrl"])
    app.post(f"/api/actions/{other['action']['id']}/complete").raise_for_status()
    with db.session() as s:
        other_txn = _txn(s, other["action"]["id"]).id
    with pytest.raises(AmfaError) as e:
        put({"amountMinor": 9000_00, "transactionId": other_txn})    # not the amount that was approved
    assert e.value.code == "BYPASS_NOT_AUTHORISED"
    pay = act(app, "transfer", payeeRef="BEN-42", amountRupees=10).json()
    verify(stack, pay["challengeUrl"])
    app.post(f"/api/actions/{pay['action']['id']}/complete").raise_for_status()
    with db.session() as s:
        pay_txn = _txn(s, pay["action"]["id"]).id
    with pytest.raises(AmfaError) as e:                              # a payment's approval is not a limit change's
        put({"amountMinor": 10_00, "transactionId": pay_txn})
    assert e.value.code == "BYPASS_NOT_AUTHORISED"


def test_a_bank_that_has_not_turned_it_on_refuses_it_and_one_cannot_exceed_its_maximum(stack, app):
    _bypass_policy(stack, enabled=False)
    done, _ = _set_nocheck(stack, app, 1000)
    assert done["status"] == "FAILED" and "BYPASS_DISABLED" in done["detail"]
    _bypass_policy(stack, enabled=True, maxMinor=4_000_00, coolingOffHours=0)
    try:
        done, _ = _set_nocheck(stack, app, 5000)
        assert done["status"] == "FAILED" and "BYPASS_ABOVE_MAX" in done["detail"]
    finally:
        _bypass_policy(stack, enabled=False)


def test_the_customer_can_turn_it_off_at_once(stack, app, bypass_on):
    _set_nocheck(stack, app, 2000)
    assert _in_force(stack, app) == 2000_00
    assert app.delete("/api/nocheck-limit").json() == {"noCheck": None}
    assert _in_force(stack, app) is None


def test_a_bank_cannot_set_a_maximum_above_the_platform_ceiling(stack):
    from pydantic import ValidationError

    from amfa.engine.policy import TenantPolicy
    with db.session() as s:
        base = s.get(db.Tenant, stack.tenant["id"]).policy
    with pytest.raises(ValidationError):
        TenantPolicy.model_validate({**base, "customerBypass": {"enabled": True, "maxMinor": 99_999_999}})


# ---- optional family helpers -------------------------------------------------------------------------------------------

HELPER_DEVICE = "helper-phone-aaaaaaaaaaaaaaaa"


def _helpers_policy(stack, **fh):
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        t.policy = {**(t.policy or {}), "familyHelpers": fh}


@pytest.fixture
def helpers_on(stack):
    _helpers_policy(stack, enabled=True, coolingOffHours=0, maxHelpers=2)
    yield
    _helpers_policy(stack, enabled=False)


def _add_helper(stack, app, label="Son"):
    started = act(app, "helper", label=label)
    assert started.status_code == 201, started.text
    verify(stack, started.json()["challengeUrl"])
    return app.post(f"/api/actions/{started.json()['action']['id']}/complete").json()["action"]


def _active_helpers(stack, app):
    from amfa.core import helpers
    with db.session() as s:
        return helpers.active_keys(s, stack.bank.CUSTOMERS[app.user]["amfaUser"], time.time() + 1)


def test_a_helper_is_added_only_after_the_checks_and_the_helper_phone_redeems_the_code(stack, app, helpers_on):
    from amfa.core.context_builder import device_key
    done = _add_helper(stack, app)
    assert done["status"] == "DONE", done["detail"]
    code = done["helperCode"]
    assert len(code) == 8 and _active_helpers(stack, app) == frozenset()          # an invite alone trusts nothing
    me = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["helpers"]
    assert [h["state"] for h in me["helpers"]] == ["INVITED"]
    wrong = app.post("/api/helpers/redeem", json={"code": "00000000" if code != "00000000" else "11111111", "deviceId": HELPER_DEVICE})
    assert wrong.status_code == 404
    ok = app.post("/api/helpers/redeem", json={"code": code, "deviceId": HELPER_DEVICE})
    assert ok.status_code == 200 and [h["state"] for h in ok.json()["helpers"]] == ["ACTIVE"]
    assert _active_helpers(stack, app) == {device_key(stack.tenant["id"], HELPER_DEVICE)}
    again = app.post("/api/helpers/redeem", json={"code": code, "deviceId": "another-phone-bbbbbbbbbbbbbb"})
    assert again.status_code == 404                                               # a code works once


def test_a_new_helper_waits_out_the_cooling_off_and_removing_one_is_immediate(stack, app):
    _helpers_policy(stack, enabled=True, coolingOffHours=24, maxHelpers=2)
    try:
        code = _add_helper(stack, app)["helperCode"]
        waiting = app.post("/api/helpers/redeem", json={"code": code, "deviceId": HELPER_DEVICE}).json()["helpers"]
        assert waiting[0]["state"] == "WAITING" and waiting[0]["activeFrom"] - time.time() > 23 * 3600
        assert _active_helpers(stack, app) == frozenset()
        with db.session() as s:
            s.get(db.TrustedHelper, waiting[0]["id"]).active_from = time.time() - 1
        assert len(_active_helpers(stack, app)) == 1
        assert app.delete(f"/api/helpers/{waiting[0]['id']}").json()["helpers"] == []
        assert _active_helpers(stack, app) == frozenset()
    finally:
        _helpers_policy(stack, enabled=False)


def test_a_helper_cannot_be_added_with_some_other_approval_or_the_same_one_twice(stack, app, helpers_on):
    AmfaError = stack.bank.AmfaError
    amfa_user = stack.bank.CUSTOMERS[app.user]["amfaUser"]
    done = _add_helper(stack, app)
    with db.session() as s:
        used = _txn(s, done["id"]).id
    post = lambda txn: stack.bank.AMFA.request("POST", f"/v1/users/{amfa_user}/helpers", {"label": "x", "transactionId": txn})
    with pytest.raises(AmfaError) as e:
        post(used)
    assert e.value.code == "HELPER_NOT_AUTHORISED"
    other = act(app, "limit", limitRupees=200_000).json()      # an approved limit change is not a helper approval
    verify(stack, other["challengeUrl"])
    app.post(f"/api/actions/{other['action']['id']}/complete").raise_for_status()
    with db.session() as s:
        other_txn = _txn(s, other["action"]["id"]).id
    with pytest.raises(AmfaError) as e:
        post(other_txn)
    assert e.value.code == "HELPER_NOT_AUTHORISED"


def test_helpers_are_optional_limited_and_off_unless_the_bank_turns_them_on(stack, app, helpers_on):
    assert app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["helpers"]["helpers"] == []   # nobody has to add one
    for who in ("A", "B"):
        assert _add_helper(stack, app, who)["status"] == "DONE"
    assert "HELPERS_FULL" in _add_helper(stack, app, "C")["detail"]
    _helpers_policy(stack, enabled=False)
    assert "HELPERS_DISABLED" in _add_helper(stack, app, "D")["detail"]


def test_a_payment_from_a_helper_phone_is_read_as_a_known_helper_by_the_risk_model(stack, app, helpers_on):
    from amfa.core.context_builder import TxnInput, assemble, device_key
    from amfa.engine.context import TxnType
    code = _add_helper(stack, app)["helperCode"]
    app.post("/api/helpers/redeem", json={"code": code, "deviceId": HELPER_DEVICE}).raise_for_status()
    amfa_user = stack.bank.CUSTOMERS[app.user]["amfaUser"]

    def asm(device):
        with db.session() as s:
            t, c = s.get(db.Tenant, stack.tenant["id"]), s.get(db.Customer, amfa_user)
            return assemble(s, t, c, "t1", TxnInput(type=TxnType.TRANSFER, amount_minor=500_00, currency="INR", payee_ref="BEN-42",
                                                    channel="MOBILE", device_id=device, ip="203.0.113.9"))
    helper, stranger = asm(HELPER_DEVICE), asm("stranger-phone-cccccccccccccc")
    assert helper.features["helper_device"] == 1 and helper.derived["device"]["helper"] and helper.ctx.device.deviceKnown
    assert stranger.features["helper_device"] == 0 and not stranger.ctx.device.deviceKnown


# ---- the learning phase: a new customer confirms payments with the phone's own fingerprint or screen lock ----------

SCORE = {"now": 0.02}


def _device_pair():
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    n = key.public_key().public_numbers()
    b = lambda v: crypto.b64e(v.to_bytes(32, "big"))
    return key, {"kty": "EC", "crv": "P-256", "x": b(n.x), "y": b(n.y)}


def _sign(key, message: str, wrong=False):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(key.sign(message.encode() + (b"x" if wrong else b""), ec.ECDSA(hashes.SHA256())))
    return crypto.b64e(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _policy_patch(stack, **changes):
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        t.policy = {**(t.policy or {}), **changes}


@pytest.fixture
def learning(stack, app, monkeypatch):
    """Learning phase on, with DEVICE a permitted factor, and the new customer's phone holding a key."""
    from amfa.engine.risk import scorer as sc
    monkeypatch.setattr(sc, "active", lambda: SimpleNamespace(score=lambda f: sc.Result(
        score=SCORE["now"], raw=SCORE["now"], level=LEVEL["now"], reasons=[("amount_ratio", .5)], version="stub")))
    SCORE["now"], LEVEL["now"] = 0.02, "T3"        # a model that is wary of an unknown customer
    old = None
    with db.session() as s:
        old = dict(s.get(db.Tenant, stack.tenant["id"]).policy or {})
    _policy_patch(stack, learning={"enabled": True}, permittedFactors=["OTP", "DEVICE", "UEBA", "FACE", "VOICE"])
    key, jwk = _device_pair()
    cid = stack.bank.CUSTOMERS[app.user]["amfaUser"]
    with db.session() as s:
        for f in ("FACE", "VOICE", "UEBA"):                      # the `app` fixture enrols everything; a new customer has none yet
            for e in s.execute(select(db.Enrollment).where(db.Enrollment.customer_id == cid, db.Enrollment.factor == f)).scalars():
                s.delete(e)
        s.add(db.Device(customer_id=cid, device_key=context_device_key(stack, PHONE_DEVICE), public_jwk=jwk, verified_count=1, platform="web"))
    app.key = key
    yield app
    _policy_patch(stack, **{k: old[k] for k in ("learning", "permittedFactors") if k in old}) if old else None
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        p = dict(t.policy)
        p.pop("learning", None)
        p.pop("permittedFactors", None)
        t.policy = p


def context_device_key(stack, device):
    from amfa.core.context_builder import device_key
    return device_key(stack.tenant["id"], device)


def _run(stack, app, started, wrong=False):
    """Answer every check the way a phone would: the key signs the device challenge, codes are read from the delivery."""
    token = started["challengeUrl"].split("#token=", 1)[1]
    auth = {"authorization": f"Bearer {token}"}
    results = []
    for _ in range(6):
        ch = stack.core.get("/c/v1/session", headers=auth).json()["challenge"]
        if ch is None:
            break
        if ch["factor"] == "DEVICE":
            payload = {"signature": _sign(app.key, ch["params"]["message"], wrong)}
        elif ch["factor"] == "OTP":
            payload = {"code": re.match(r"(\d+)", SENT[-1]).group(1)}
        else:
            payload = {}
        r = stack.core.post(f"/c/v1/session/factors/{ch['factor']}", headers=auth,
                            json={"challengeId": ch["challengeId"], "payload": payload})
        results.append((ch["factor"], r.status_code, r.json().get("result", {}).get("status")))
        if r.status_code != 200:
            break
    return results


def test_a_new_customer_pays_with_only_the_phones_fingerprint(stack, learning):
    app = learning
    before = balance(app)
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=1500).json()
    assert started["action"]["checks"] == ["fingerprint or screen lock"]        # no face, no voice, no typing, not even a code
    assert _run(stack, app, started) == [("DEVICE", 200, "PASSED")]
    done = app.post(f"/api/actions/{started['action']['id']}/complete").json()
    assert done["action"]["status"] == "DONE" and done["balance"] == before - 1500_00
    assert "Fingerprint or screen lock" in done["action"]["confirmedWith"]


def test_a_wrong_signature_does_not_pass_and_three_of_them_end_the_attempt(stack, learning):
    app = learning
    before = balance(app)
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=1500).json()
    results = _run(stack, app, started, wrong=True)
    assert len(results) == 2 and results[-1][2] == "FAILED" and "PASSED" not in [r[2] for r in results]   # one retry, then it ends
    out = app.post(f"/api/actions/{started['action']['id']}/complete").json()
    assert out["action"]["status"] != "DONE" and balance(app) == before


def test_a_payment_beyond_the_learning_limits_also_needs_a_code(stack, learning):
    app = learning
    started = act(app, "transfer", payeeRef="BEN-42", amountRupees=30_000).json()
    assert set(started["action"]["checks"]) == {"fingerprint or screen lock", "one-time code"}
    assert [r[2] for r in _run(stack, app, started)] == ["PASSED", "PASSED"]
    assert app.post(f"/api/actions/{started['action']['id']}/complete").json()["action"]["status"] == "DONE"


def test_while_learning_no_face_or_voice_is_asked_even_when_the_model_is_wary(stack, learning):
    app = learning
    for amount in (1500, 12_000, 30_000):
        a = act(app, "transfer", payeeRef="BEN-42", amountRupees=amount).json()["action"]
        assert "face check" not in a["checks"] and "voice check" not in a["checks"], (amount, a["checks"])


def test_a_payment_the_model_is_nearly_sure_is_fraud_is_held_for_a_person(stack, learning):
    app = learning
    SCORE["now"] = 0.8
    a = act(app, "transfer", payeeRef="BEN-42", amountRupees=1500).json()["action"]
    assert a["reviewAfter"]


def test_a_fraud_report_sends_the_customer_back_to_the_start(stack, learning):
    from amfa.core import phase
    cid = stack.bank.CUSTOMERS[learning.user]["amfaUser"]
    with db.session() as s:
        row = phase.ensure(s, s.get(db.Customer, cid))
        row.phase, row.ready_since, row.verified_payments = "READY", time.time(), 30
    LEVEL["now"] = "T1"
    started = act(learning, "transfer", payeeRef="BEN-42", amountRupees=1500).json()
    assert "one-time code" in started["action"]["checks"]                       # ready: the model decides, no longer the light plan
    _run(stack, learning, started)
    learning.post(f"/api/actions/{started['action']['id']}/complete").raise_for_status()
    learning.post(f"/api/actions/{started['action']['id']}/report", json={"reason": "not me"}).raise_for_status()
    with db.session() as s:
        assert s.get(db.CustomerPhase, cid).phase == "LEARNING"


def test_the_customer_learns_from_each_verified_payment(stack, learning):
    cid = stack.bank.CUSTOMERS[learning.user]["amfaUser"]
    for _ in range(2):
        started = act(learning, "transfer", payeeRef="BEN-42", amountRupees=1500).json()
        _run(stack, learning, started)
        learning.post(f"/api/actions/{started['action']['id']}/complete").raise_for_status()
    with db.session() as s:
        row = s.get(db.CustomerPhase, cid)
        assert row.verified_payments == 2 and row.phase == "LEARNING" and len(row.evidence["logs"]) == 2


def test_the_bank_can_show_how_far_the_model_is_from_knowing_the_customer(stack, learning):
    me = learning.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()
    assert me["learning"]["phase"] == "LEARNING" and me["learning"]["progress"]["moreAtLeast"] > 0
    assert me["learning"]["offerBiometrics"] is False
    started = act(learning, "transfer", payeeRef="BEN-42", amountRupees=1500).json()
    _run(stack, learning, started)
    learning.post(f"/api/actions/{started['action']['id']}/complete").raise_for_status()
    after = learning.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["learning"]
    assert after["verifiedPayments"] == 1 and after["progress"]["payments"] == 1


def test_the_phones_own_key_is_listed_among_the_ways_the_customer_can_confirm(stack, learning):
    me = learning.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()
    assert "Fingerprint or screen lock" in me["protection"]["have"] and "Fingerprint or screen lock" not in me["protection"]["canAdd"]


# ---- payee reputation: what the bank's other customers say about the payee ---------------------------------------

@pytest.fixture
def reputation_on(stack):
    _policy_patch(stack, payeeReputation={"enabled": True})
    yield
    with db.session() as s:
        t = s.get(db.Tenant, stack.tenant["id"])
        p = dict(t.policy)
        p.pop("payeeReputation", None)
        t.policy = p


def _saved_payee(stack, app, account):
    started = act(app, "payee", name="Ravi Kumar", account=account, ifsc="HDFC0001234").json()
    verify(stack, started["challengeUrl"])
    app.post(f"/api/actions/{started['action']['id']}/complete").raise_for_status()
    return _ref("HDFC0001234", account)


def _others_paid(stack, ref, n, first_ago_days=3):
    from amfa.core.context_builder import payee_key
    key = payee_key(stack.tenant["id"], ref)
    with db.session() as s:
        for _ in range(n):
            cid = f"cu_{uuid.uuid4().hex[:12]}"
            s.add(db.Customer(id=cid, tenant_id=stack.tenant["id"], external_ref=uuid.uuid4().hex[:12]))
            s.flush()
            s.add(db.KnownPayee(customer_id=cid, payee_key=key, first_seen=time.time() - first_ago_days * 86400, count=1))


def test_a_payee_that_many_other_customers_paid_this_week_is_held_and_explained(stack, app, reputation_on):
    LEVEL["now"] = "T1"
    ref = _saved_payee(stack, app, "123456780777")
    quiet = act(app, "transfer", payeeRef=ref, amountRupees=20_000).json()["action"]
    assert not quiet["reviewAfter"]                                           # nobody else has paid it: an ordinary new payee
    _others_paid(stack, ref, 7)
    flagged = act(app, "transfer", payeeRef=ref, amountRupees=20_000).json()["action"]
    assert flagged["reviewAfter"] and "drawn warnings from other customers" in (flagged["reason"] or "")


def test_a_bank_that_has_not_turned_it_on_sees_no_difference(stack, app):
    LEVEL["now"] = "T1"
    ref = _saved_payee(stack, app, "123456780888")
    _others_paid(stack, ref, 7)
    assert not act(app, "transfer", payeeRef=ref, amountRupees=20_000).json()["action"]["reviewAfter"]


def test_a_reviewer_can_see_why_a_payee_is_flagged(stack, app, reputation_on):
    ref = _saved_payee(stack, app, "123456780999")
    _others_paid(stack, ref, 6)
    seen = stack.bank.AMFA.request("GET", f"/v1/payees/reputation?payeeRef={ref}")
    assert seen["enabled"] and seen["flag"] and seen["senders7d"] == 7 and seen["reports"] == 0     # the six, and the customer who saved it
    assert not stack.bank.AMFA.request("GET", "/v1/payees/reputation?payeeRef=NOBODY-HAS-PAID-THIS")["flag"]


# ---- the sign-up questions ----------------------------------------------------------------------------------------

def test_the_answers_to_the_sign_up_questions_are_kept_shown_and_can_be_taken_back(stack, app):
    me = app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()
    assert me["profile"]["declared"] is False and len(me["profile"]["typicalLabels"]) == 5
    saved = app.post("/api/profile", json={"monthBand": 1, "typicalBand": 2, "regularPayees": ["BEN-42", "NOT-MINE"]}).json()
    assert saved["declared"] and saved["monthBand"] == 1 and saved["typicalBand"] == 2 and saved["regularPayees"] == 1   # only a payee they really have
    assert app.get("/api/me", params={"deviceId": PHONE_DEVICE}).json()["profile"]["typicalBand"] == 2
    assert app.delete("/api/profile").json()["declared"] is False


def test_an_answer_outside_the_bands_is_refused(stack, app):
    assert app.post("/api/profile", json={"typicalBand": 9}).status_code == 422
    assert app.post("/api/profile", json={"monthBand": -1}).status_code == 422


def test_erasing_a_customer_removes_their_answers_and_their_learning_state(stack, app, monkeypatch):
    monkeypatch.setattr(factors, "erase", lambda *a, **k: True)             # the biometric services are not running here
    cid = stack.bank.CUSTOMERS[app.user]["amfaUser"]
    app.post("/api/profile", json={"typicalBand": 1}).raise_for_status()
    stack.bank.AMFA.request("DELETE", f"/v1/users/{cid}")
    with db.session() as s:
        assert s.get(db.CustomerProfile, cid) is None and s.get(db.CustomerPhase, cid) is None
