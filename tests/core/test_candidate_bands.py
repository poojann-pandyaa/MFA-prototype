"""#7 phases 4 and 5: a candidate model earns each band while it watches, two people trust it there, and promotion moves the bands
that trusted it with no gap and sends every other band on the old model back to the rules. Nothing here may ever make a band
trust a version that was not graded and approved for it."""
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from amfa.core import audit, band_gate, db
from amfa.core.app import create_app
from amfa.core.auth import COOKIE, new_operator_session
from amfa.engine import band_switch
from amfa.engine.context import (Band, BehaviouralCtx, Context, DeviceCtx, Enrollment, OrgCtx, Plan, Policy, TransactionCtx,
                                 TxnType)
from amfa.engine.decide import decide
from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy
from amfa.engine.risk import scorer

IST = ZoneInfo("Asia/Kolkata")
DAY, WEEK = 86_400.0, 7 * 86_400.0
NOW = datetime(2026, 11, 18, 14, 30, tzinfo=IST).timestamp()
V1, V2, V3 = "risk-20261117-v1", "risk-20261201-v2", "risk-20261215-v3"
MIN = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4)
POLICY = TenantPolicy(collection=CollectionPolicy(enabled=True, gate=MIN, topGate=MIN))


class Models:
    """The active and candidate versions as the whole system sees them, and the pointer switch promotion makes."""
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


@pytest.fixture
def models(monkeypatch):
    return Models(monkeypatch)


def _tenant(s, enabled=True):
    t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="P", slug=f"p{uuid.uuid4().hex[:8]}",
                  policy=TenantPolicy(collection=CollectionPolicy(enabled=enabled, gate=MIN, topGate=MIN)).model_dump(mode="json"))
    s.add(t)
    s.flush()
    return t.id


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        return _tenant(s)


def last_mature_week(now=NOW):
    return date_of(band_gate.window(now, IST, 8)[1])


def date_of(iso):
    return datetime.fromisoformat(iso).date()


def seed(tenant_id, band=1, *, weeks=16, ahead=8, genuine=300, fraud=8, active=("T1", V1), candidate=("T1", V2), rules="T2",
         fraud_active="T2", fraud_candidate="T2"):
    """Rows for [weeks] Mondays back from the newest mature week (shifted [ahead] weeks on, so later gradings still find data).
    Genuine rows carry the active and candidate levels given; fraud rows the fraud levels; the rules ask [rules] throughout."""
    start = last_mature_week() + timedelta(weeks=ahead)
    out = []
    for w in range(weeks):
        week = (start - timedelta(weeks=w)).isoformat()
        for i in range(genuine + fraud):
            bad = i >= genuine
            out.append(db.LearningRow(
                id=uuid.uuid4().hex, tenant_id=tenant_id, band=band, band_vs_usual="SAME", payee_kind="KNOWN", device_kind="KNOWN",
                hour_bucket="MORNING", hard_sign=False, rules_level=rules,
                model_level=fraud_active if bad else active[0], model_version=active[1],
                candidate_level=(fraud_candidate if bad else candidate[0]) if candidate else None,
                candidate_version=candidate[1] if candidate else None,
                mode="COLLECTING", outcome="COMPLETED", label="FRAUD" if bad else "UNKNOWN", label_source="bank_ops" if bad else None,
                week_start=week))
    with db.session() as s:
        s.add_all(out)


def evaluate(tenant_id, now=NOW):
    with db.session() as s:
        band_gate.evaluate(s, tenant_id, POLICY, now)


def cand(tenant_id, band=1):
    with db.session() as s:
        bc = s.get(db.BandCandidate, (tenant_id, band))
        return None if bc is None else (bc.version, bc.state, bc.streak, dict(bc.figures))


def band(tenant_id, b=1):
    with db.session() as s:
        st = s.get(db.BandGateState, (tenant_id, b))
        return None if st is None else (st.state, st.model_version, st.streak)


def set_band(tenant_id, b, state, version, streak=4):
    with db.session() as s:
        st = s.get(db.BandGateState, (tenant_id, b)) or db.BandGateState(tenant_id=tenant_id, band=b, figures={}, changed_at=NOW)
        st.state, st.model_version, st.streak, st.evaluated_at = state, version, streak, time.time()
        s.merge(st)


def set_cand(tenant_id, b, state, version=V2, streak=4):
    with db.session() as s:
        s.merge(db.BandCandidate(tenant_id=tenant_id, band=b, version=version, state=state, streak=streak, figures={},
                                 changed_at=NOW, evaluated_at=time.time()))


# ---- phase 4: grading the candidate on its own answers ---------------------------------------------------------------------------

def test_the_candidate_is_graded_on_its_own_answers_not_the_active_models(bank, models):
    # the active model bothers every honest customer (T2); the candidate bothers none (T0) and catches all fraud
    seed(bank, weeks=1, ahead=0, genuine=40, fraud=4, active=("T2", V1), candidate=("T0", V2))
    evaluate(bank)
    f = cand(bank)[3]
    assert (f["genuine"], f["fraud"], f["model_false"], f["model_caught"]) == (40, 4, 0, 4)
    with db.session() as s:
        assert s.get(db.BandGateState, (bank, 1)).figures["model_false"] == 40        # the active model's own grading


def test_grading_the_candidate_never_moves_the_band_itself(bank, models):
    set_band(bank, 1, "MODEL", V1)
    seed(bank, active=("T1", V1), candidate=("T0", V2))
    for w in range(5):
        evaluate(bank, NOW + w * WEEK)
    assert band(bank)[:2] == ("MODEL", V1) and cand(bank)[1] == "ELIGIBLE"


def test_four_weekly_passes_make_the_candidate_eligible_and_a_loss_takes_it_and_any_approval_back(bank, models):
    seed(bank, candidate=("T0", V2))
    states = []
    for w in range(4):
        evaluate(bank, NOW + w * WEEK)
        states.append(cand(bank)[1:3])
    assert states == [("COLLECTING", 1), ("COLLECTING", 2), ("COLLECTING", 3), ("ELIGIBLE", 4)]
    with db.session() as s:
        bc = s.get(db.BandCandidate, (bank, 1))
        bc.state, bc.approved_by = "APPROVED", "op_a,op_b"
    with db.session() as s:                                           # now it misses fraud the rules catch
        s.add_all([db.LearningRow(id=uuid.uuid4().hex, tenant_id=bank, band=1, band_vs_usual="SAME", payee_kind="KNOWN",
                                  device_kind="KNOWN", hour_bucket="MORNING", hard_sign=False, rules_level="T2", model_level="T2",
                                  model_version=V1, candidate_level="T0", candidate_version=V2, mode="COLLECTING",
                                  outcome="COMPLETED", label="FRAUD", label_source="bank_ops",
                                  week_start=band_gate.window(NOW + 4 * WEEK, IST, 8)[1]) for _ in range(3)])
    evaluate(bank, NOW + 4 * WEEK)
    v, st, streak, f = cand(bank)
    assert (st, streak) == ("COLLECTING", 0) and "the model missed more fraud than the rules" in f["missing"]
    with db.session() as s:
        assert s.get(db.BandCandidate, (bank, 1)).approved_by is None


def test_a_candidate_that_misses_one_fraud_more_than_the_rules_is_not_eligible(bank, models):
    seed(bank, candidate=("T0", V2))
    with db.session() as s:
        s.add(db.LearningRow(id=uuid.uuid4().hex, tenant_id=bank, band=1, band_vs_usual="SAME", payee_kind="KNOWN",
                             device_kind="KNOWN", hour_bucket="MORNING", hard_sign=False, rules_level="T2", model_level="T2",
                             model_version=V1, candidate_level="T1", candidate_version=V2, mode="COLLECTING", outcome="COMPLETED",
                             label="FRAUD", label_source="bank_ops", week_start=band_gate.window(NOW, IST, 8)[1]))
    for w in range(5):
        evaluate(bank, NOW + w * WEEK)
    assert cand(bank)[1] == "COLLECTING"


def test_a_new_candidate_starts_from_nothing_and_a_removed_one_leaves_nothing(bank, models):
    seed(bank, candidate=("T0", V2))
    for w in range(4):
        evaluate(bank, NOW + w * WEEK)
    assert cand(bank)[1] == "ELIGIBLE"
    models.candidate = V3
    evaluate(bank, NOW + 4 * WEEK)
    assert cand(bank)[:3] == (V3, "COLLECTING", 0)                      # its rows have no V3 answers yet
    replaced = _audit(bank, "band_candidate.replaced")
    assert [e.detail["from"] for e in replaced if e.target == f"{bank}:1"] == [V2]
    assert {e.target for e in replaced} == {f"{bank}:{b}" for b in range(4)}         # tracked, and replaced, in every band
    models.candidate = None
    evaluate(bank, NOW + 5 * WEEK)
    assert cand(bank) is None


def _audit(tenant_id, action):
    with db.session() as s:
        return s.execute(select(db.AuditLog).where(db.AuditLog.tenant_id == tenant_id, db.AuditLog.action == action)).scalars().all()


def test_a_promoted_candidate_keeps_the_evidence_it_built_while_watching(bank, models):
    """After promotion its answers as candidate still count, so it does not start from zero on the day it takes over."""
    seed(bank, weeks=1, ahead=0, genuine=40, fraud=4, active=("T2", V1), candidate=("T0", V2))
    models.active, models.candidate = V2, None
    evaluate(bank)
    with db.session() as s:
        f = s.get(db.BandGateState, (bank, 1)).figures
    assert (f["genuine"], f["fraud"], f["model_false"]) == (40, 4, 0)       # graded on its candidate answers


@pytest.mark.parametrize("row, version, level", [
    (dict(model_version=V1, model_level="T1", candidate_version=V2, candidate_level="T3"), V1, "T1"),
    (dict(model_version=V1, model_level="T1", candidate_version=V2, candidate_level="T3"), V2, "T3"),
    (dict(model_version=V1, model_level="T1", candidate_version=None, candidate_level=None), V2, None),
    (dict(model_version=V1, model_level="T1", candidate_version=V2, candidate_level="T3"), V3, None),
])
def test_level_for_picks_the_answer_of_the_version_being_graded(row, version, level):
    assert band_gate.level_for(type("R", (), row)(), version) == level


# ---- phase 4: two people trust the candidate in a band -------------------------------------------------------------------------

def test_two_different_people_trust_an_eligible_candidate_in_a_band(bank, models):
    set_cand(bank, 1, "ELIGIBLE")
    with db.session() as s:
        rid = band_gate.request_candidate(s, bank, 1, "op_a").id
    with db.session() as s, pytest.raises(band_gate.GateError) as e:
        band_gate.approve(s, rid, "op_a")
    assert e.value.code == "FOUR_EYES"
    with db.session() as s:
        band_gate.approve(s, rid, "op_b")
    assert cand(bank)[1] == "APPROVED"
    [entry] = _audit(bank, "band_candidate.approved")
    assert (entry.detail["version"], entry.detail["requestedBy"], entry.detail["approvedBy"]) == (V2, "op_a", "op_b")
    assert band(bank) is None or band(bank)[0] != "MODEL"               # trusting the candidate moves nothing until promotion


@pytest.mark.parametrize("state", ["COLLECTING", "APPROVED", None])
def test_only_an_eligible_candidate_can_be_requested(bank, models, state):
    if state:
        set_cand(bank, 1, state)
    with db.session() as s, pytest.raises(band_gate.GateError) as e:
        band_gate.request_candidate(s, bank, 1, "op_a")
    assert e.value.code == "NOT_ELIGIBLE"


def test_no_request_without_a_candidate(bank, models):
    models.candidate = None
    with db.session() as s, pytest.raises(band_gate.GateError) as e:
        band_gate.request_candidate(s, bank, 1, "op_a")
    assert e.value.code == "NO_CANDIDATE"


def test_a_request_for_one_candidate_cannot_be_approved_for_another(bank, models):
    set_cand(bank, 1, "ELIGIBLE")
    with db.session() as s:
        rid = band_gate.request_candidate(s, bank, 1, "op_a").id
    models.candidate = V3
    with db.session() as s, pytest.raises(band_gate.GateError) as e:
        band_gate.approve(s, rid, "op_b")
    assert e.value.code == "MODEL_CHANGED" and cand(bank)[1] == "ELIGIBLE"


# ---- phase 5: promotion --------------------------------------------------------------------------------------------------------

def outcome(preview, tenant_id):
    bank = next(b for b in preview["banks"] if b["tenantId"] == tenant_id)
    return {r["band"]: (r["now"], r["candidate"], r["after"], r["drop"]) for r in bank["bands"]}


def test_the_preview_says_what_each_band_will_do(bank, models):
    set_band(bank, 0, "MODEL", V1)
    set_cand(bank, 0, "APPROVED")
    set_band(bank, 1, "MODEL", V1)
    set_cand(bank, 1, "ELIGIBLE")
    set_cand(bank, 2, "ELIGIBLE")
    with db.session() as s:
        p = band_gate.promotion_preview(s)
    o = outcome(p, bank)
    assert o[0] == ("MODEL", "APPROVED", "MODEL", False)
    assert o[1] == ("MODEL", "ELIGIBLE", "ELIGIBLE", True)               # on the old model, not trusted for the new: a drop
    assert o[2] == ("COLLECTING", "ELIGIBLE", "ELIGIBLE", False)
    assert o[3] == ("COLLECTING", "COLLECTING", "COLLECTING", False)
    assert {(d["tenantId"], d["band"]) for d in p["drops"]} >= {(bank, 1)}
    assert (p["active"], p["candidate"]) == (V1, V2)


def test_banks_with_the_switch_off_are_not_in_the_preview(models):
    db.migrate()
    with db.session() as s:
        off = _tenant(s, enabled=False)
    with db.session() as s:
        assert off not in {b["tenantId"] for b in band_gate.promotion_preview(s)["banks"]}


def test_no_preview_or_promotion_without_a_candidate(bank, models):
    models.candidate = None
    with db.session() as s, pytest.raises(band_gate.GateError):
        band_gate.promotion_preview(s)
    with pytest.raises(band_gate.GateError) as e:
        band_gate.promote("op_platform")
    assert e.value.code == "NO_CANDIDATE" and models.promoted == []


def _drops_only_here(bank):
    """Other tests leave banks behind in the shared database; promotion looks at all of them. Turn theirs off."""
    with db.session() as s:
        for t in s.execute(select(db.Tenant)).scalars().all():
            if t.id != bank and TenantPolicy.model_validate(t.policy).collection.enabled:
                p = dict(t.policy)
                p["collection"] = dict(p["collection"]) | {"enabled": False}
                t.policy = p


def test_promotion_is_refused_while_it_would_drop_a_band_unless_confirmed(bank, models):
    _drops_only_here(bank)
    set_band(bank, 1, "MODEL", V1)
    with pytest.raises(band_gate.GateError) as e:
        band_gate.promote("op_platform")
    assert e.value.code == "WOULD_DROP" and models.promoted == [] and band(bank)[:2] == ("MODEL", V1)
    band_gate.promote("op_platform", confirm_drops=True)
    assert models.promoted == [V2] and band(bank)[:2] == ("COLLECTING", V2)
    with db.session() as s:
        assert s.execute(select(db.SecurityEvent).where(db.SecurityEvent.tenant_id == bank,
                                                        db.SecurityEvent.kind == "BAND_MODEL_CHANGED")).scalars().first()


def test_a_broken_candidate_is_never_promoted(bank, models, monkeypatch):
    _drops_only_here(bank)
    monkeypatch.setattr(scorer, "candidate", lambda: None)
    with pytest.raises(band_gate.GateError) as e:
        band_gate.promote("op_platform", confirm_drops=True)
    assert e.value.code == "CANDIDATE_BROKEN" and models.promoted == []


def test_promotion_moves_trusted_bands_with_no_gap_and_carries_the_candidates_progress(bank, models):
    _drops_only_here(bank)
    set_band(bank, 0, "MODEL", V1)
    set_cand(bank, 0, "APPROVED", streak=6)
    set_cand(bank, 2, "ELIGIBLE", streak=5)
    set_cand(bank, 3, "COLLECTING", streak=2)
    with db.session() as s:
        s.add(db.BandSwitchRequest(id=f"bsr_{uuid.uuid4().hex[:12]}", tenant_id=bank, kind="CANDIDATE_FOR_BAND", band=2,
                                   payload={"candidateVersion": V2}, status="PENDING", requested_by="op_a", requested_at=NOW,
                                   expires_at=time.time() + 3600))
    seen_before_pointer = {}

    def promote(version, store=None):                                   # the pointer moves only after the states are stored
        seen_before_pointer["band0"] = band(bank, 0)
        models.promoted.append(version)
        models.active, models.candidate = version, None
    import amfa.engine.risk.scorer as sc
    original, sc.promote_candidate = sc.promote_candidate, promote
    try:
        band_gate.promote("op_platform")
    finally:
        sc.promote_candidate = original
    assert seen_before_pointer["band0"][:2] == ("MODEL", V2)
    assert band(bank, 0) == ("MODEL", V2, 6)
    assert band(bank, 2) == ("ELIGIBLE", V2, 5)
    assert band(bank, 3) == ("COLLECTING", V2, 2)
    assert band(bank, 1)[:2] == ("COLLECTING", V2)
    assert all(cand(bank, b) is None for b in range(4))                 # the candidate is used up
    with db.session() as s:
        assert {r.status for r in s.execute(select(db.BandSwitchRequest).where(db.BandSwitchRequest.tenant_id == bank)).scalars()} \
            == {"CANCELLED"}
    assert len(_audit(bank, "band_gate.promoted")) == 4
    assert audit.verify()["intact"]


def test_if_promotion_stops_before_the_pointer_moves_every_band_fails_closed(bank, models, monkeypatch):
    _drops_only_here(bank)
    set_band(bank, 0, "MODEL", V1)
    set_cand(bank, 0, "APPROVED")

    def crash(version, store=None):
        raise scorer.RiskModelError("disk full")
    monkeypatch.setattr(scorer, "promote_candidate", crash)
    with pytest.raises(scorer.RiskModelError):
        band_gate.promote("op_platform")
    assert band(bank, 0)[:2] == ("MODEL", V2) and models.active == V1     # states moved, pointer did not
    # every decision: approved on V2, scored by V1, so the rules set the minimum
    stub = scorer.Result(score=0.01, raw=0.01, level="T0", reasons=[], version=V1, habit="T0")
    monkeypatch.setattr(scorer, "active", lambda: type("M", (), {"score": lambda self, f: stub, "version": V1})())
    with db.session() as s:
        state, approved = band_gate.decision_state(s, bank, 0, time.time())
    c = Context(userId="u", transactionId="t", transaction=TransactionCtx(amountBand=Band.LOW, exposureBand=Band.LOW,
                                                                           exposureMinor=500_00, type=TxnType.TRANSFER),
                device=DeviceCtx(), behavioural=BehaviouralCtx(), enrollment=Enrollment(), org=OrgCtx(),
                policy=Policy(bandSwitch=True, bandState=state, bandModelVersion=approved))
    assert decide(c, features={}).bandState == "COLLECTING"
    # and the night's grading takes the band back
    evaluate(bank, time.time())
    assert band(bank, 0)[:2] == ("COLLECTING", V1)


def test_promoting_back_to_an_older_version_is_a_new_version_that_must_be_trusted_again(bank, models):
    _drops_only_here(bank)
    set_band(bank, 0, "MODEL", V1)
    set_cand(bank, 0, "APPROVED")
    band_gate.promote("op_platform")
    assert band(bank, 0)[:2] == ("MODEL", V2)
    models.candidate = V1                                               # the old version comes back as a candidate
    with pytest.raises(band_gate.GateError) as e:
        band_gate.promote("op_platform")
    assert e.value.code == "WOULD_DROP"                                 # V1's old approval does not count


# ---- the platform endpoints --------------------------------------------------------------------------------------------------------

def _operator(s, tenant_id, role):
    op = db.Operator(id=f"op_{uuid.uuid4().hex[:12]}", tenant_id=tenant_id, email=f"{uuid.uuid4().hex[:10]}@bank.test",
                     password_hash="x", role=role, totp_confirmed=True)
    s.add(op)
    s.flush()
    cookie, csrf = new_operator_session(s, op, "FULL")
    return {"cookie": cookie, "csrf": csrf}


def _call(c, op, method, path, json=None):
    c.cookies.set(COOKIE, op["cookie"])
    r = c.request(method, f"/admin/api{path}", json=json, headers={"x-csrf-token": op["csrf"]})
    c.cookies.clear()
    return r


def test_only_a_platform_operator_sees_the_preview_or_promotes(bank, models):
    _drops_only_here(bank)
    with db.session() as s:
        bank_admin, platform = _operator(s, bank, "tenant_admin"), _operator(s, None, "platform_admin")
    with TestClient(create_app()) as c:
        assert _call(c, bank_admin, "GET", "/risk/candidate").status_code == 403
        assert _call(c, bank_admin, "POST", "/risk/candidate/promote", {"confirmDrops": True}).status_code == 403
        r = _call(c, platform, "GET", "/risk/candidate")
        assert r.status_code == 200 and r.json()["candidate"] == V2
        set_band(bank, 1, "MODEL", V1)
        r = _call(c, platform, "POST", "/risk/candidate/promote", {})
        assert r.status_code == 409 and r.json()["error"]["code"] == "WOULD_DROP"
        assert _call(c, platform, "POST", "/risk/candidate/promote", {"confirmDrops": True}).status_code == 200
    assert models.promoted == [V2]


def test_a_bank_operator_requests_the_candidate_for_a_band_through_the_console(bank, models):
    set_cand(bank, 2, "ELIGIBLE")
    with db.session() as s:
        a, b = _operator(s, bank, "tenant_admin"), _operator(s, bank, "compliance")
    with TestClient(create_app()) as c:
        rid = _call(c, a, "POST", "/band-gates/2/request-candidate").json()["id"]
        assert _call(c, a, "POST", f"/band-gates/requests/{rid}/approve").json()["error"]["code"] == "FOUR_EYES"
        assert _call(c, b, "POST", f"/band-gates/requests/{rid}/approve").status_code == 200
        view = _call(c, a, "GET", "/band-gates").json()["bands"]
    assert cand(bank, 2)[1] == "APPROVED"
    assert next(v for v in view if v["band"] == 2)["candidate"]["state"] == "APPROVED"


# ---- the model store's own promotion --------------------------------------------------------------------------------------------

def test_the_store_promotes_only_its_installed_candidate_and_then_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(scorer, "_load", lambda path: type("M", (), {"version": path})())
    for v in (V1, V2):
        (tmp_path / v).mkdir()
    (tmp_path / "ACTIVE").write_text(V1)
    with pytest.raises(scorer.RiskModelError):
        scorer.promote_candidate(V2, tmp_path)                          # not installed as the candidate
    (tmp_path / "CANDIDATE").write_text(V2)
    scorer.promote_candidate(V2, tmp_path)
    assert (tmp_path / "ACTIVE").read_text() == V2 and not (tmp_path / "CANDIDATE").exists()
