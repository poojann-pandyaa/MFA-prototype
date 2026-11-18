"""#3: the band switch as its own service. The same band behaviour inside the core and through the service; only the core can call
the service, and nothing identifying can reach it; when it is slow or down every payment gets the stricter plan, rows and labels wait
in the outbox and arrive exactly once, and operator actions are refused without being half-applied; the one-off move."""
import base64
import dataclasses
import json
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from amfa import config
from amfa.bands import db as bdb
from amfa.bands import engine, move, remote, service
from amfa.bands import store as band_store
from amfa.common import signing
from amfa.core import band_gate, db, learning_store
from amfa.core.app import create_app
from amfa.core.auth import COOKIE, new_operator_session
from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy
from amfa.engine.risk import scorer
from tests.core import bands_remote as R

IST = ZoneInfo("Asia/Kolkata")
DAY, WEEK = 86_400.0, 7 * 86_400.0
NOW = datetime(2026, 11, 18, 14, 30, tzinfo=IST).timestamp()
V1, V2 = "risk-20261117-v1", "risk-20261201-v2"
MIN = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4)
POLICY = TenantPolicy(collection=CollectionPolicy(enabled=True, gate=MIN, topGate=MIN))


@pytest.fixture(autouse=True)
def one_model_decides(monkeypatch):
    monkeypatch.setattr(band_gate, "active_version", lambda: V1)


@pytest.fixture(params=["local", "remote"])
def where(request, monkeypatch):
    """Where the band data lives: the core's own database (the default), or the band service's own database behind signed calls."""
    db.migrate()
    if request.param == "local":
        yield SimpleNamespace(mode="local", session=db.session, client=None)
        return
    client = R.start(monkeypatch)
    try:
        yield SimpleNamespace(mode="remote", session=bdb.session, client=client)
    finally:
        R.stop()


@pytest.fixture
def svc(monkeypatch):
    db.migrate()
    client = R.start(monkeypatch)
    try:
        yield client
    finally:
        R.stop()


def _tenant(s, enabled=True):
    t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="S", slug=f"s{uuid.uuid4().hex[:8]}",
                  policy=TenantPolicy(collection=CollectionPolicy(enabled=enabled, gate=MIN, topGate=MIN)).model_dump(mode="json"))
    s.add(t)
    s.flush()
    return t.id


def _operator(s, tenant_id, role="tenant_admin"):
    op = db.Operator(id=f"op_{uuid.uuid4().hex[:12]}", tenant_id=tenant_id, email=f"{uuid.uuid4().hex[:10]}@bank.test",
                     password_hash="x", role=role, totp_confirmed=True)
    s.add(op)
    s.flush()
    cookie, csrf = new_operator_session(s, op, "FULL")
    return {"id": op.id, "cookie": cookie, "csrf": csrf}


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        tid = _tenant(s)
        ops = {"a": _operator(s, tid), "b": _operator(s, tid, "compliance")}
    return tid, ops


def fields(tenant_id, band=1, link=None, **kw):
    """A row exactly as the core builds it (amfa/core/learning_store._record)."""
    return dict(tenant_id=tenant_id, band=band, band_vs_usual="SAME", payee_kind="KNOWN", device_kind="KNOWN", hour_bucket="MORNING",
                hard_sign=False, rules_level="T1", model_level="T1", model_version=V1, candidate_level=None, candidate_version=None,
                mode="COLLECTING", outcome="COMPLETED", label="UNKNOWN", label_source=None, week_start="2026-11-16",
                link=link or secrets.token_hex(32), link_expires_at=time.time() + 90 * DAY) | kw


def seed(where, tenant_id, band=0, *, weeks=16, ahead=8, genuine=300, fraud=8):
    start = date.fromisoformat(band_gate.window(NOW, IST, 8)[1]) + timedelta(weeks=ahead)
    rows = []
    for w in range(weeks):
        week = (start - timedelta(weeks=w)).isoformat()
        for i in range(genuine + fraud):
            bad = i >= genuine
            rows.append(db.LearningRow(id=uuid.uuid4().hex, **(fields(tenant_id, band, week_start=week, rules_level="T2",
                                                                       model_level="T2" if bad else "T1",
                                                                       label="FRAUD" if bad else "UNKNOWN",
                                                                       label_source="bank_ops" if bad else None)
                                                                | {"link": None, "link_expires_at": None})))
    with where.session() as bs:
        bs.add_all(rows)


def set_band(where, tenant_id, band, state, version=V1, evaluated_at=None):
    with where.session() as bs:
        st = bs.get(db.BandGateState, (tenant_id, band)) or db.BandGateState(tenant_id=tenant_id, band=band, figures={},
                                                                              changed_at=time.time())
        st.state, st.model_version, st.streak = state, version, 4
        st.evaluated_at = time.time() if evaluated_at is None else evaluated_at
        bs.merge(st)


def band_of(where, tenant_id, band):
    with where.session() as bs:
        st = bs.get(db.BandGateState, (tenant_id, band))
        return None if st is None else (st.state, st.model_version)


def audit_actions(tenant_id):
    with db.session() as s:
        return [e.action for e in s.execute(select(db.AuditLog).where(db.AuditLog.tenant_id == tenant_id)
                                            .order_by(db.AuditLog.at)).scalars()]


def security_events(tenant_id):
    with db.session() as s:
        return [(e.kind, e.severity) for e in s.execute(select(db.SecurityEvent).where(db.SecurityEvent.tenant_id == tenant_id))
                .scalars()]


def core_rows(tenant_id):
    with db.session() as s:
        return s.execute(select(func.count()).select_from(db.LearningRow).where(db.LearningRow.tenant_id == tenant_id)).scalar_one()


def add_row(f, now=None):
    with db.session() as s:
        row, events = band_store.for_session(s).add_row(f, now or time.time())
        band_gate.apply_events(s, events)
        return row


# ---- the same behaviour in both places ---------------------------------------------------------------------------------------------

def test_grading_makes_a_band_eligible_after_four_weekly_passes(where, bank):
    tid, _ = bank
    seed(where, tid)
    states = []
    for w in range(4):
        with db.session() as s:
            out = band_gate.evaluate(s, tid, POLICY, NOW + w * WEEK)
        states.append((out[0].state, out[0].streak))
    assert states == [("COLLECTING", 1), ("COLLECTING", 2), ("COLLECTING", 3), ("ELIGIBLE", 4)]
    assert out[0].figures["genuine"] == 8 * 300 and out[0].figures["fraud"] == 8 * 8
    assert band_of(where, tid, 0) == ("ELIGIBLE", V1)
    assert "band_gate.eligible" in audit_actions(tid)                   # written into the core's audit log, in both places
    if where.mode == "remote":
        assert core_rows(tid) == 0                                      # and the rows never were in the core's database


def test_two_operators_move_a_band_to_the_model_and_one_sends_it_back(where, bank):
    tid, ops = bank
    set_band(where, tid, 0, "ELIGIBLE")
    with db.session() as s:
        rid = band_gate.request_model(s, tid, 0, ops["a"]["id"]).id
    assert band_of(where, tid, 0) == ("ELIGIBLE", V1)
    with db.session() as s:
        with pytest.raises(band_gate.GateError) as e:
            band_gate.approve(s, rid, ops["a"]["id"])
        assert e.value.code == "FOUR_EYES"
    with db.session() as s:
        band_gate.approve(s, rid, ops["b"]["id"])
    assert band_of(where, tid, 0) == ("MODEL", V1)
    with db.session() as s:
        assert band_gate.decision_state(s, tid, 0) == ("MODEL", V1)
        assert band_gate.revert(s, tid, 0, ops["a"]["id"])
    assert band_of(where, tid, 0)[0] == "COLLECTING"
    with db.session() as s:
        assert band_gate.decision_state(s, tid, 0) == ("COLLECTING", None)
    acts = audit_actions(tid)
    assert "band_gate.model" in acts and "band_gate.fallback" in acts
    assert ("BAND_SWITCH_FALLBACK", "MEDIUM") in security_events(tid)


def test_a_model_band_that_moved_on_to_another_version_is_cancelled_at_approval(where, bank, monkeypatch):
    tid, ops = bank
    set_band(where, tid, 1, "ELIGIBLE")
    with db.session() as s:
        rid = band_gate.request_model(s, tid, 1, ops["a"]["id"]).id
    monkeypatch.setattr(band_gate, "active_version", lambda: V2)
    with db.session() as s:
        with pytest.raises(band_gate.GateError) as e:
            band_gate.approve(s, rid, ops["b"]["id"])
    assert e.value.code == "MODEL_CHANGED" and band_of(where, tid, 1) == ("ELIGIBLE", V1)


def test_a_fraud_the_model_let_through_in_the_lakh_band_sends_it_back_at_once(where, bank):
    tid, _ = bank
    set_band(where, tid, 3, "MODEL")
    add_row(fields(tid, 3, link=learning_store.link_of("txn_lakh"), mode="MODEL", model_level="T0", rules_level="T4"))
    with db.session() as s:
        assert learning_store.apply_label(s, "txn_lakh", "FRAUD", "chargeback")
    assert band_of(where, tid, 3)[0] == "COLLECTING"
    assert ("BAND_SWITCH_FALLBACK", "HIGH") in security_events(tid)


def test_a_row_written_twice_for_the_same_payment_is_kept_once(where, bank):
    tid, _ = bank
    link = secrets.token_hex(32)
    assert add_row(fields(tid, link=link)) is not None
    assert add_row(fields(tid, link=link)) is None
    with where.session() as bs:
        assert bs.execute(select(func.count()).select_from(db.LearningRow).where(db.LearningRow.link == link)).scalar_one() == 1


def test_a_label_reaches_its_row_and_not_after_its_link_has_expired(where, bank):
    tid, _ = bank
    add_row(fields(tid, link=learning_store.link_of("txn_fresh")))
    add_row(fields(tid, link=learning_store.link_of("txn_old"), link_expires_at=time.time() - 1))
    with db.session() as s:
        assert learning_store.apply_label(s, "txn_fresh", "FRAUD", "customer_report")
        assert not learning_store.apply_label(s, "txn_old", "FRAUD", "customer_report")
        assert not learning_store.apply_label(s, "txn_fresh", "FRAUD", "simulator")       # not a real source
    with where.session() as bs:
        row = bs.execute(select(db.LearningRow).where(db.LearningRow.link == learning_store.link_of("txn_fresh"))).scalar_one()
        assert (row.label, row.label_source) == ("FRAUD", "customer_report")


def test_purge_wipes_expired_links_and_rows_past_retention(where, bank):
    tid, _ = bank
    add_row(fields(tid, link=learning_store.link_of("txn_ancient"), week_start="2020-01-06"))
    add_row(fields(tid, link=learning_store.link_of("txn_stale_link"), link_expires_at=time.time() - 1))
    with db.session() as s:
        out = learning_store.purge(s)
    assert out["learning_row"] >= 1 and out["learning_link"] >= 1
    with where.session() as bs:
        rows = bs.execute(select(db.LearningRow).where(db.LearningRow.tenant_id == tid)).scalars().all()
        assert len(rows) == 1 and rows[0].link is None and rows[0].week_start == "2026-11-16"


class Models:
    def __init__(self, monkeypatch):
        self.active, self.candidate, self.promoted = V1, V2, []
        monkeypatch.setattr(band_gate, "active_version", lambda: self.active)
        monkeypatch.setattr(scorer, "candidate_version", lambda store=None: self.candidate)
        monkeypatch.setattr(scorer, "candidate", lambda: type("C", (), {"version": self.candidate})() if self.candidate else None)

        def promote(version, store=None):
            if version != self.candidate:
                raise scorer.RiskModelError("not the candidate")
            self.promoted.append(version)
            self.active, self.candidate = version, None
        monkeypatch.setattr(scorer, "promote_candidate", promote)


def test_a_candidate_trusted_by_two_operators_takes_the_band_over_at_promotion_with_no_gap(where, bank, monkeypatch):
    tid, ops = bank
    models = Models(monkeypatch)
    set_band(where, tid, 1, "MODEL", V1)
    with where.session() as bs:
        bs.merge(db.BandCandidate(tenant_id=tid, band=1, version=V2, state="ELIGIBLE", streak=4, figures={}, changed_at=NOW,
                                  evaluated_at=time.time()))
    with db.session() as s:
        rid = band_gate.request_candidate(s, tid, 1, ops["a"]["id"]).id
    with db.session() as s:
        band_gate.approve(s, rid, ops["b"]["id"])
    with where.session() as bs:
        assert bs.get(db.BandCandidate, (tid, 1)).state == "APPROVED"
    band_gate.promote("op_platform", confirm_drops=True)
    assert models.promoted == [V2] and band_of(where, tid, 1) == ("MODEL", V2)
    assert {"band_candidate.approved", "band_gate.promoted"} <= set(audit_actions(tid))


def test_the_admin_view_reads_every_band_from_wherever_it_lives(where, bank):
    tid, ops = bank
    set_band(where, tid, 2, "ELIGIBLE")
    with TestClient(create_app()) as c:
        c.cookies.set(COOKIE, ops["a"]["cookie"])
        r = c.get("/admin/api/band-gates", headers={"x-csrf-token": ops["a"]["csrf"]})
    assert r.status_code == 200
    states = {b["band"]: b["state"] for b in r.json()["bands"]}
    assert states[2] == "ELIGIBLE" and states[0] == "COLLECTING"


# ---- only the core can call it -------------------------------------------------------------------------------------------------

def _signed(path, body=None, *, raw=None, secret=None, key_id="core", ts=None, nonce=None):
    raw = raw if raw is not None else json.dumps(body).encode()
    h = signing.sign(secret or config.get().internal_token, "POST", path, "", raw, key_id, ts=ts, nonce=nonce)
    return raw, h | {"content-type": "application/json"}


def _code(r):
    return r.json()["error"]["code"]


def test_an_unsigned_request_is_refused_on_every_endpoint_before_its_body_is_read(svc):
    paths = [r.path for r in service.app.routes if getattr(r, "path", "").startswith("/v1/")]
    assert len(paths) == 14
    for p in paths:
        r = svc.post(p, content=b'{"anything": "at all", "customer_id": "c1"}', headers={"content-type": "application/json"})
        assert r.status_code == 401 and _code(r) == "SIGNATURE_MISSING", p


@pytest.mark.parametrize("how, code", [
    ({"key_id": "bank"}, "UNKNOWN_CALLER"),
    ({"secret": "x" * 48}, "SIGNATURE_MISMATCH"),
    ({"ts": int(time.time()) - 3600}, "TIMESTAMP_OUT_OF_WINDOW"),
])
def test_a_request_not_signed_by_the_core_is_refused(svc, how, code):
    raw, h = _signed("/v1/due", {"tenantId": "t", "now": 1.0}, **how)
    r = svc.post("/v1/due", content=raw, headers=h)
    assert r.status_code == 401 and _code(r) == code


def test_a_body_changed_after_signing_is_refused(svc):
    raw, h = _signed("/v1/due", {"tenantId": "t", "now": 1.0})
    r = svc.post("/v1/due", content=raw.replace(b'"t"', b'"u"'), headers=h)
    assert r.status_code == 401 and _code(r) == "SIGNATURE_MISMATCH"


def test_a_signature_for_one_endpoint_does_not_work_on_another(svc):
    raw, h = _signed("/v1/due", {"tenantId": "t"})
    r = svc.post("/v1/reset", content=raw, headers=h)
    assert r.status_code == 401 and _code(r) == "SIGNATURE_MISMATCH"


def test_a_replayed_request_is_refused(svc):
    raw, h = _signed("/v1/due", {"tenantId": "t", "now": 1.0})
    assert svc.post("/v1/due", content=raw, headers=h).status_code == 200
    again = svc.post("/v1/due", content=raw, headers=h)
    assert again.status_code == 401 and _code(again) == "NONCE_REPLAYED"


@pytest.mark.parametrize("extra", [{"customer_id": "cus_1"}, {"amount_minor": 250000}, {"payee_ref": "BEN-42"},
                                   {"device_id": "dev_1"}, {"transaction_id": "txn_1"}, {"ip": "10.0.0.1"}])
def test_a_row_carrying_anything_identifying_is_refused_and_nothing_is_stored(svc, bank, extra):
    tid, _ = bank
    f = fields(tid) | extra
    raw, h = _signed("/v1/rows", {"fields": f, "now": time.time()})
    r = svc.post("/v1/rows", content=raw, headers=h)
    assert r.status_code == 422
    with bdb.session() as bs:
        assert bs.execute(select(func.count()).select_from(db.LearningRow).where(db.LearningRow.tenant_id == tid)).scalar_one() == 0


@pytest.mark.parametrize("bad", [{"link": "not-a-keyed-hash"}, {"link": "A" * 64}, {"band": 4}, {"label": "MAYBE"},
                                 {"label_source": "simulator"}, {"rules_level": "T9"}, {"week_start": "last week"},
                                 {"payee_kind": "BEN-42"}, {"tenant_id": ""}])
def test_a_row_with_a_value_outside_its_pattern_is_refused(svc, bank, bad):
    tid, _ = bank
    raw, h = _signed("/v1/rows", {"fields": fields(tid) | bad, "now": time.time()})
    assert svc.post("/v1/rows", content=raw, headers=h).status_code == 422


def test_an_extra_field_next_to_the_row_is_refused_too(svc, bank):
    tid, _ = bank
    raw, h = _signed("/v1/rows", {"fields": fields(tid), "now": time.time(), "customer": "cus_1"})
    assert svc.post("/v1/rows", content=raw, headers=h).status_code == 422
    raw, h = _signed("/v1/labels", {"link": secrets.token_hex(32), "label": "FRAUD", "source": "chargeback", "now": 1.0,
                                    "transactionId": "txn_1"})
    assert svc.post("/v1/labels", content=raw, headers=h).status_code == 422


def test_the_engine_itself_refuses_a_field_that_is_not_a_band_row_field():
    with pytest.raises(ValueError):
        engine.add_row(None, fields("t") | {"customer_id": "c1"}, time.time(), engine.Events())


def test_a_body_over_one_megabyte_is_refused(svc):
    raw, h = _signed("/v1/due", raw=b'{"tenantId": "' + b"x" * (1024 * 1024 + 10) + b'", "now": 1}')
    assert svc.post("/v1/due", content=raw, headers=h).status_code == 413


def test_it_publishes_no_api_description_and_answers_health_without_a_signature(svc):
    assert svc.get("/healthz").status_code == 200
    for p in ("/docs", "/openapi.json", "/redoc"):
        assert svc.get(p).status_code == 404


def test_in_production_it_refuses_to_start_under_any_role_but_its_own(monkeypatch):
    s = dataclasses.replace(config.get(), env="production", role="core")
    monkeypatch.setattr(config, "get", lambda: s)
    with pytest.raises(RuntimeError, match="AMFA_ROLE=bands"):
        with TestClient(service.app):
            pass


def _production_env(monkeypatch, **env):
    base = {"AMFA_ENV": "production", "AMFA_ROLE": "bands", "AMFA_INTERNAL_TOKEN": secrets.token_urlsafe(48),
            "AMFA_MASTER_KEYS": "v1:" + base64.b64encode(secrets.token_bytes(32)).decode(), "AMFA_REDIS_URL": "redis://r:6379/0",
            "AMFA_BANDS_DB_URL": "postgresql+psycopg://amfa_bands:pw@postgres-bands:5432/amfa_bands"}
    monkeypatch.delenv("AMFA_DATABASE_URL", raising=False)
    monkeypatch.delenv("AMFA_MASTER_KEY", raising=False)
    for k, v in (base | env).items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def test_the_bands_role_loads_in_production_with_its_own_postgres(monkeypatch):
    _production_env(monkeypatch)
    assert config.load().role == "bands"


@pytest.mark.parametrize("env, says", [
    ({"AMFA_BANDS_DB_URL": None}, "AMFA_BANDS_DB_URL to PostgreSQL"),
    ({"AMFA_BANDS_DB_URL": "sqlite:///bands.db"}, "AMFA_BANDS_DB_URL to PostgreSQL"),
    ({"AMFA_DATABASE_URL": "postgresql+psycopg://amfa_app:pw@postgres-core/amfa_core"}, "must not be given AMFA_DATABASE_URL"),
    ({"AMFA_ROLE": "bandz"}, "AMFA_ROLE must be"),
])
def test_the_bands_role_refuses_an_unsafe_production_setting(monkeypatch, env, says):
    _production_env(monkeypatch, **env)
    with pytest.raises(config.ConfigError, match=says):
        config.load()


# ---- failing closed ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("service_", [R.Down(), R.Down(httpx.ReadTimeout("slow")), R.Answers(500, {"error": "x"}),
                                      R.Answers(401, {"error": "x"}), R.Answers(200, b"<html>not json"), R.Answers(200, [1, 2]),
                                      R.Answers(200, {"state": "MODEL"}), R.Answers(200, {"state": "MODEL", "version": ""}),
                                      R.Answers(200, {"state": "SOMETHING_ELSE", "version": V1}), R.Answers(200, {})],
                         ids=["down", "timeout", "500", "401", "not-json", "a-list", "model-no-version", "model-empty-version",
                              "unknown-state", "empty"])
def test_a_payment_is_decided_under_the_rules_whenever_the_service_does_not_answer_properly(svc, bank, service_):
    tid, _ = bank
    set_band(SimpleNamespace(session=bdb.session), tid, 0, "MODEL")
    remote.mount(service_)
    with db.session() as s:
        assert band_gate.decision_state(s, tid, 0) == ("COLLECTING", None)
    assert service_.calls == 1


def test_the_decision_call_waits_no_longer_than_the_set_timeout(svc, bank):
    tid, _ = bank
    seen = {}

    class Spy:
        def post(self, path, **kw):
            seen[path] = kw["timeout"]
            return svc.post(path, **kw)
    remote.mount(Spy())
    with db.session() as s:
        band_gate.decision_state(s, tid, 0)
    assert seen["/v1/decision-state"] == config.get().bands_timeout_s == 0.2


def test_over_a_real_network_a_slow_service_costs_the_payment_at_most_the_timeout_and_the_stricter_plan(svc, bank, monkeypatch):
    import socket

    import uvicorn
    tid, _ = bank
    set_band(SimpleNamespace(session=bdb.session), tid, 0, "MODEL")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(service.app, host="127.0.0.1", port=port, log_config=None, lifespan="on"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        s_ = dataclasses.replace(config.get(), bands_url=f"http://127.0.0.1:{port}")
        monkeypatch.setattr(config, "get", lambda: s_)
        remote.mount(None)
        remote._client = None
        with db.session() as s:
            assert band_gate.decision_state(s, tid, 0) == ("MODEL", V1)           # a real signed round trip
        real = engine.decision_state
        monkeypatch.setattr(engine, "decision_state", lambda *a: (time.sleep(1.5), real(*a))[1])
        with db.session() as s:
            started = time.monotonic()
            assert band_gate.decision_state(s, tid, 0) == ("COLLECTING", None)
            assert time.monotonic() - started < 0.8
    finally:
        server.should_exit = True
        t.join(5)
        if remote._client is not None:
            remote._client.close()
        remote._client = None


def test_with_the_service_down_grading_is_skipped_and_a_model_band_goes_back_to_the_rules_once_its_grading_is_stale(svc, bank):
    tid, _ = bank
    bands = SimpleNamespace(session=bdb.session)
    set_band(bands, tid, 0, "MODEL", evaluated_at=time.time() - 47 * 3600)
    with db.session() as s:
        assert band_gate.decision_state(s, tid, 0) == ("MODEL", V1)
    remote.mount(R.Down())
    with db.session() as s:
        assert band_gate.run_due(s) == 0                                          # nothing graded, nothing raised
    remote.mount(svc)
    with db.session() as s:
        assert band_gate.decision_state(s, tid, 0, now=time.time() + 2 * 3600) == ("COLLECTING", None)


def _admin(c, op, method, path):
    c.cookies.set(COOKIE, op["cookie"])
    r = c.request(method, f"/admin/api{path}", headers={"x-csrf-token": op["csrf"]})
    c.cookies.clear()
    return r


def test_operator_actions_are_refused_with_503_while_the_service_is_down_and_nothing_is_half_applied(svc, bank):
    tid, ops = bank
    set_band(SimpleNamespace(session=bdb.session), tid, 0, "ELIGIBLE")
    with TestClient(create_app()) as c:
        rid = _admin(c, ops["a"], "POST", "/band-gates/0/request-model").json()["id"]
        remote.mount(R.Down())
        for method, path in [("GET", "/band-gates"), ("POST", "/band-gates/1/request-model"),
                             ("POST", f"/band-gates/requests/{rid}/approve"), ("POST", "/band-gates/0/revert")]:
            r = _admin(c, ops["b"], method, path)
            assert r.status_code == 503 and _code(r) == "BANDS_UNAVAILABLE", path
        remote.mount(svc)
        with db.session() as s:
            assert s.get(db.BandSwitchRequest, rid).status == "PENDING"            # still waiting for its second person
        assert band_of(SimpleNamespace(session=bdb.session), tid, 0) == ("ELIGIBLE", V1)
        ok = _admin(c, ops["b"], "POST", f"/band-gates/requests/{rid}/approve")
        assert ok.status_code == 200 and band_of(SimpleNamespace(session=bdb.session), tid, 0) == ("MODEL", V1)


def test_a_promotion_with_the_service_down_never_reaches_the_pointer_switch(svc, bank, monkeypatch):
    models = Models(monkeypatch)
    remote.mount(R.Down())
    with pytest.raises(remote.BandsUnavailable):
        band_gate.promote("op_platform", confirm_drops=True)
    assert models.promoted == [] and models.active == V1


# ---- the outbox: nothing lost, nothing twice, nothing kept past its time -------------------------------------------------------------

def outbox():
    with db.session() as s:
        return s.execute(select(db.BandOutbox).order_by(db.BandOutbox.created_at)).scalars().all()


@pytest.fixture
def empty_outbox():
    with db.session() as s:
        for it in s.execute(select(db.BandOutbox)).scalars():
            s.delete(it)
    yield
    with db.session() as s:
        for it in s.execute(select(db.BandOutbox)).scalars():
            s.delete(it)


def band_rows(link):
    with bdb.session() as bs:
        return bs.execute(select(db.LearningRow).where(db.LearningRow.link == link)).scalars().all()


def test_a_row_and_its_label_written_while_the_service_is_down_wait_and_arrive_once_in_order(svc, bank, empty_outbox):
    tid, _ = bank
    link = learning_store.link_of("txn_outage")
    remote.mount(R.Down())
    f = fields(tid, link=link)
    assert add_row(f) is None
    with db.session() as s:
        assert not learning_store.apply_label(s, "txn_outage", "FRAUD", "chargeback")
    held = outbox()
    assert [h.kind for h in held] == ["ROW", "LABEL"] and held[0].expires_at == f["link_expires_at"]
    assert band_rows(link) == []
    remote.mount(svc)
    with db.session() as s:
        assert remote.flush_outbox(s) == {"sent": 2, "waiting": 0, "dropped": 0}
    rows = band_rows(link)
    assert len(rows) == 1 and (rows[0].label, rows[0].label_source) == ("FRAUD", "chargeback")
    assert outbox() == []
    with db.session() as s:
        assert remote.flush_outbox(s) == {"sent": 0, "waiting": 0, "dropped": 0}
    assert len(band_rows(link)) == 1


def test_a_row_the_service_took_although_the_core_saw_an_error_is_still_kept_once(svc, bank, empty_outbox):
    tid, _ = bank
    f = fields(tid)
    remote.call("/v1/rows", {"fields": f, "now": time.time()})                   # it arrived; the answer was lost
    with db.session() as s:
        s.add(db.BandOutbox(id=uuid.uuid4().hex, kind="ROW", payload={"fields": f, "now": time.time()}, attempts=0,
                            next_at=time.time(), expires_at=f["link_expires_at"], created_at=time.time()))
    with db.session() as s:
        assert remote.flush_outbox(s)["sent"] == 1
    assert len(band_rows(f["link"])) == 1


def test_a_held_row_whose_link_would_have_expired_is_dropped_unsent(svc, bank, empty_outbox):
    tid, _ = bank
    remote.mount(R.Down())
    add_row(fields(tid, link_expires_at=time.time() + 60))
    spy = R.Answers(200, {"stored": True, "id": "x", "events": []})
    remote.mount(spy)
    with db.session() as s:
        assert remote.flush_outbox(s, now=time.time() + 120) == {"sent": 0, "waiting": 0, "dropped": 1}
    assert spy.calls == 0 and outbox() == []


def test_while_still_down_a_held_item_is_tried_less_and_less_often(svc, bank, empty_outbox):
    tid, _ = bank
    down = R.Down()
    remote.mount(down)
    add_row(fields(tid))
    now = time.time() + 1
    with db.session() as s:
        assert remote.flush_outbox(s, now)["waiting"] == 1
    assert (outbox()[0].attempts, round(outbox()[0].next_at - now)) == (1, 30)
    with db.session() as s:
        assert remote.flush_outbox(s, now + 10) == {"sent": 0, "waiting": 0, "dropped": 0}        # not due yet: not tried
    with db.session() as s:
        remote.flush_outbox(s, now + 31)
    assert (outbox()[0].attempts, round(outbox()[0].next_at - (now + 31))) == (2, 120)
    assert down.calls == 1 + 2                                                    # the write itself, then two retries


def test_what_a_held_row_changes_when_it_arrives_is_recorded_in_the_core(svc, bank, empty_outbox):
    tid, _ = bank
    set_band(SimpleNamespace(session=bdb.session), tid, 3, "MODEL")
    remote.mount(R.Down())
    add_row(fields(tid, 3, mode="MODEL", model_level="T0", rules_level="T4", label="FRAUD", label_source="chargeback"))
    assert band_of(SimpleNamespace(session=bdb.session), tid, 3)[0] == "MODEL"
    remote.mount(svc)
    with db.session() as s:
        remote.flush_outbox(s)
    assert band_of(SimpleNamespace(session=bdb.session), tid, 3)[0] == "COLLECTING"
    assert ("BAND_SWITCH_FALLBACK", "HIGH") in security_events(tid) and "band_gate.fallback" in audit_actions(tid)


def test_the_outbox_holds_only_the_rows_categories_never_the_payment(svc, bank, empty_outbox):
    tid, _ = bank
    remote.mount(R.Down())
    add_row(fields(tid))
    held = outbox()[0].payload
    assert set(held) == {"fields", "now"} and set(held["fields"]) == engine.ROW_FIELDS


# ---- moving existing band data out of the core ------------------------------------------------------------------------------------

@pytest.fixture
def own_core(monkeypatch, tmp_path):
    """A core database of its own for the move, so the move empties only it."""
    s = dataclasses.replace(config.get(), database_url=f"sqlite:///{(tmp_path / 'core.db').as_posix()}")
    monkeypatch.setattr(config, "get", lambda: s)
    db.engine.cache_clear()
    db._factory.cache_clear()
    db.migrate()
    R._fresh_db(monkeypatch)
    yield
    db.engine().dispose()
    db.engine.cache_clear()
    db._factory.cache_clear()
    R.reset_db()


def _seed_core(n=2500):
    with db.session() as s:
        tid = _tenant(s)
        s.add_all([db.LearningRow(id=uuid.uuid4().hex, **fields(tid)) for _ in range(n)])
        s.add(db.BandGateState(tenant_id=tid, band=1, state="MODEL", model_version=V1, streak=4, figures={"genuine": 1},
                               changed_at=1.0, evaluated_at=2.0))
        s.add(db.BandCandidate(tenant_id=tid, band=1, version=V2, state="ELIGIBLE", streak=4, figures={}, changed_at=1.0))
    return tid


def _count(session, model):
    with session() as s:
        return s.execute(select(func.count()).select_from(model)).scalar_one()


def test_the_move_counts_first_then_copies_everything_checks_it_and_only_then_empties_the_core(own_core):
    tid = _seed_core()
    dry = move.move(dry_run=True)
    assert dry["tables"]["learning_row"] == {"inCore": 2500} and _count(bdb.session, db.LearningRow) == 0
    out = move.move()
    assert out["tables"]["learning_row"] == {"inCore": 2500, "copied": 2500} and out["deletedFromCore"]
    assert out["tables"]["band_gate"]["copied"] == 1 and out["tables"]["band_candidate"]["copied"] == 1
    assert [_count(db.session, m) for m in move.TABLES] == [0, 0, 0]
    assert _count(bdb.session, db.LearningRow) == 2500
    with bdb.session() as bs:
        st = bs.get(db.BandGateState, (tid, 1))
        assert (st.state, st.model_version, st.figures, st.evaluated_at) == ("MODEL", V1, {"genuine": 1}, 2.0)


def test_running_the_move_again_copies_nothing_twice(own_core):
    _seed_core(10)
    move.move()
    _seed_core(5)
    out = move.move()
    assert out["tables"]["learning_row"] == {"inCore": 5, "copied": 5}
    assert _count(bdb.session, db.LearningRow) == 15


def test_if_anything_is_missing_after_the_copy_the_core_keeps_every_row(own_core, monkeypatch):
    _seed_core(10)
    monkeypatch.setattr(move, "_missing", lambda model, src, dst: 1 if model is db.LearningRow else 0)
    with pytest.raises(move.MoveError, match="nothing deleted"):
        move.move()
    assert _count(db.session, db.LearningRow) == 10


def test_the_move_refuses_to_copy_the_core_into_itself(own_core, monkeypatch):
    monkeypatch.setenv("AMFA_BANDS_DB_URL", config.get().database_url)
    with pytest.raises(move.MoveError, match="core's own database"):
        move.move()
