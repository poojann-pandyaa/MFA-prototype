"""Phase 3 of the band switch: grading each band (rules against model, on the same payments) and the gate to ELIGIBLE. On fixed data.
Nothing here may ever move a band to MODEL or change a decision."""
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from amfa.core import audit, band_gate, db
from amfa.engine.policy import BandGate, CollectionPolicy, TenantPolicy

IST = ZoneInfo("Asia/Kolkata")
DAY, WEEK = 86_400.0, 7 * 86_400.0
NOW = datetime(2026, 11, 18, 14, 30, tzinfo=IST).timestamp()          # a Wednesday
SMALL_GATE = BandGate(minGenuine=2_000, minFraud=50, minWeeks=4, passingEvaluations=4)    # the hard minimums, to keep data small


_BANK = object()


def R(label="UNKNOWN", outcome="COMPLETED", model="T1", rules="T1", week="2026-10-05", source=_BANK):
    if source is _BANK:
        source = "bank_ops" if label != "UNKNOWN" else None
    return SimpleNamespace(label=label, label_source=source, outcome=outcome, model_level=model, rules_level=rules, week_start=week)


# ---- the arithmetic -------------------------------------------------------------------------------------------------------------

def test_grading_counts_fraud_caught_and_false_alarms_for_both_on_the_same_payments():
    rows = [R("FRAUD", model="T2", rules="T2"), R("FRAUD", model="T3", rules="T1"), R("FRAUD", model="T0", rules="T4"),
            R("FRAUD", model="T1", rules="T1", outcome="DECLINED"),
            R(model="T2", rules="T1"), R(model="T1", rules="T2"), R(model="T1", rules="T3"), R("GENUINE", model="T0", rules="T0"),
            R(outcome="DECLINED"), R(outcome="CANCELLED"), R(outcome="EXPIRED")]                    # nobody knows: ignored
    g = band_gate.grade(rows)
    assert (g.fraud, g.genuine) == (4, 4)
    assert (g.model_caught, g.rules_caught) == (2, 2)
    assert (g.model_only_missed, g.rules_only_missed) == (1, 1)
    assert (g.model_false, g.rules_false) == (1, 2)
    assert g.weeks == 1


@pytest.mark.parametrize("source", ["simulator", "customer_app", None, ""])
def test_labels_not_from_the_bank_or_review_never_count(source):
    g = band_gate.grade([R("FRAUD", source=source), R("GENUINE", source=source, outcome="DECLINED")])
    assert (g.fraud, g.genuine) == (0, 0)


def test_weeks_are_counted_only_from_graded_rows():
    g = band_gate.grade([R(week="2026-09-07"), R(week="2026-09-14"), R(outcome="EXPIRED", week="2026-09-21")])
    assert g.weeks == 2


# ---- the gate ---------------------------------------------------------------------------------------------------------------------

def good(**kw):
    base = dict(genuine=5_000, fraud=100, weeks=8, model_caught=90, rules_caught=85, model_only_missed=2, rules_only_missed=7,
                model_false=150, rules_false=400)
    return band_gate.Grade(**(base | kw))


def test_a_band_with_enough_evidence_where_the_model_wins_passes():
    ok, why = band_gate.passes(good(), BandGate(), top=False)
    assert ok and why == []


@pytest.mark.parametrize("kw, reason", [
    ({"genuine": 4_999}, "genuine payments"),
    ({"fraud": 99}, "fraud reports"),
    ({"weeks": 7}, "separate weeks"),
    ({"model_only_missed": 8, "rules_only_missed": 7}, "missed more fraud"),     # one more miss than the rules
    ({"model_false": 401}, "honest customers"),
])
def test_each_gate_on_its_own_keeps_the_band_collecting(kw, reason):
    ok, why = band_gate.passes(good(**kw), BandGate(), top=False)
    assert not ok and len(why) == 1 and reason in why[0]


def test_equal_to_the_rules_is_enough_for_bands_0_to_2():
    assert band_gate.passes(good(model_only_missed=7, rules_only_missed=7, model_false=400), BandGate(), top=False)[0]


def test_the_lakh_band_needs_double_the_evidence_and_no_single_fraud_let_through():
    top = CollectionPolicy().topGate
    assert not band_gate.passes(good(genuine=9_999, fraud=200), top, top=True)[0]
    assert not band_gate.passes(good(genuine=10_000, fraud=199), top, top=True)[0]
    ok, why = band_gate.passes(good(genuine=10_000, fraud=200, model_only_missed=1, rules_only_missed=9), top, top=True)
    assert not ok and why == ["the model let through a fraud the rules would have stopped"]
    assert band_gate.passes(good(genuine=10_000, fraud=200, model_only_missed=0), top, top=True)[0]


# ---- the window -----------------------------------------------------------------------------------------------------------------

def test_the_window_is_the_latest_weeks_whose_payments_are_all_at_least_30_days_old():
    first, last = band_gate.window(NOW, IST, 8)
    assert (first, last) == ("2026-08-24", "2026-10-12")
    # the newest graded week ends 2026-10-18; a payment that Sunday is 31 days old on 2026-11-18
    assert (datetime(2026, 11, 18, tzinfo=IST).date() - date(2026, 10, 18)).days >= 30
    # the week after it is not: its last day is 2026-10-25, 24 days ago
    assert date.fromisoformat(last) + timedelta(weeks=1) > datetime(2026, 11, 18, tzinfo=IST).date() - timedelta(days=37)


# ---- with the database ------------------------------------------------------------------------------------------------------------

def _policy(gate=SMALL_GATE, enabled=True):
    return TenantPolicy(collection=CollectionPolicy(enabled=enabled, gate=gate, topGate=gate))


@pytest.fixture
def bank():
    db.migrate()
    with db.session() as s:
        t = db.Tenant(id=f"tn_{uuid.uuid4().hex[:12]}", name="G", slug=f"g{uuid.uuid4().hex[:8]}",
                      policy=_policy().model_dump(mode="json"))
        s.add(t)
    return t.id


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def seed(tenant_id, band=0, *, weeks, genuine_per_week=300, fraud_per_week=8, fraud=("T2", "T2"), genuine=("T1", "T2"),
         source="bank_ops", start=None, ahead=0):
    """Rows for [weeks] Mondays going back from [start] (default: the newest mature week at NOW, or [ahead] weeks after it, so
    gradings over the next weeks still find a full window). (model, rules) levels per kind."""
    start = start or date.fromisoformat(band_gate.window(NOW, IST, 8)[1]) + timedelta(weeks=ahead)
    with db.session() as s:
        rows = []
        for w in range(weeks):
            week = (start - timedelta(weeks=w)).isoformat()
            for _ in range(genuine_per_week):
                rows.append(_row(tenant_id, band, week, "UNKNOWN", None, *genuine))
            for _ in range(fraud_per_week):
                rows.append(_row(tenant_id, band, week, "FRAUD", source, *fraud))
        s.add_all(rows)


def _row(tenant_id, band, week, label, source, model, rules):
    return db.LearningRow(id=uuid.uuid4().hex, tenant_id=tenant_id, band=band, band_vs_usual="SAME", payee_kind="KNOWN",
                          device_kind="KNOWN", hour_bucket="MORNING", hard_sign=False, rules_level=rules, model_level=model,
                          mode="COLLECTING", outcome="COMPLETED", label=label, label_source=source, week_start=week)


def evaluate(tenant_id, now=NOW, policy=None):
    with db.session() as s:
        out = band_gate.evaluate(s, tenant_id, policy or _policy(), now)
        return {b: (st.state, st.streak, dict(st.figures)) for b, st in out.items()}


def test_rows_too_young_to_have_been_reported_are_not_graded(bank):
    young = date.fromisoformat(band_gate.window(NOW, IST, 8)[1]) + timedelta(weeks=1)
    seed(bank, weeks=1, start=young, genuine_per_week=50, fraud_per_week=5)
    seed(bank, weeks=1, genuine_per_week=20, fraud_per_week=2)
    f = evaluate(bank)[0][2]
    assert (f["genuine"], f["fraud"]) == (20, 2)


def test_a_completed_payment_counts_as_genuine_only_once_it_is_30_days_old_and_a_later_report_turns_it_to_fraud(bank):
    # a payment completed on Sunday 2026-10-18 (week of 2026-10-12): 29 days later it is not graded, 31 days later it is
    with db.session() as s:
        s.add(_row(bank, 1, "2026-10-12", "UNKNOWN", None, "T1", "T1"))
    day29 = datetime(2026, 11, 16, 12, tzinfo=IST).timestamp()
    day31 = datetime(2026, 11, 18, 12, tzinfo=IST).timestamp()
    assert evaluate(bank, now=day29)[1][2]["genuine"] == 0
    assert evaluate(bank, now=day31)[1][2]["genuine"] == 1
    with db.session() as s:                                   # a fraud report arrives on day 40
        row = s.execute(select(db.LearningRow).where(db.LearningRow.tenant_id == bank, db.LearningRow.band == 1)).scalar_one()
        row.label, row.label_source = "FRAUD", "chargeback"
    f = evaluate(bank, now=day31 + 9 * DAY)[1][2]
    assert (f["genuine"], f["fraud"]) == (0, 1)


def test_four_weekly_passes_in_a_row_make_a_band_eligible_and_never_model(bank):
    seed(bank, weeks=16, ahead=8)            # enough for every window over the next 4 weeks
    states = [evaluate(bank, now=NOW + w * WEEK)[0][:2] for w in range(4)]
    assert states == [("COLLECTING", 1), ("COLLECTING", 2), ("COLLECTING", 3), ("ELIGIBLE", 4)]
    for w in range(4, 8):
        assert evaluate(bank, now=NOW + w * WEEK)[0][0] == "ELIGIBLE"           # stays eligible; nothing here moves it to MODEL


def test_grading_every_night_counts_once_a_week(bank):
    seed(bank, weeks=16, ahead=8)
    for night in range(7):                                                     # a whole week of nightly gradings
        state, streak, _ = evaluate(bank, now=NOW - 2 * DAY + night * DAY)[0]
    assert (state, streak) in {("COLLECTING", 1), ("COLLECTING", 2)}           # the week turns over once in these 7 nights
    assert streak <= 2


def test_three_passes_and_one_failure_start_the_count_again(bank):
    seed(bank, weeks=16, ahead=8)
    for w in range(3):
        evaluate(bank, now=NOW + w * WEEK)
    strict = _policy(gate=BandGate(minGenuine=10_000_000, minFraud=50, minWeeks=4, passingEvaluations=4))
    assert evaluate(bank, now=NOW + 3 * WEEK, policy=strict)[0][:2] == ("COLLECTING", 0)
    assert evaluate(bank, now=NOW + 4 * WEEK)[0][:2] == ("COLLECTING", 1)


def test_an_eligible_band_that_starts_failing_goes_back_to_collecting(bank):
    seed(bank, weeks=16, ahead=8)
    for w in range(4):
        evaluate(bank, now=NOW + w * WEEK)
    with db.session() as s:                                   # the model starts missing fraud the rules catch
        s.add_all([_row(bank, 0, date.fromisoformat(band_gate.window(NOW + 4 * WEEK, IST, 8)[1]).isoformat(), "FRAUD", "bank_ops",
                        "T1", "T2") for _ in range(3)])
    state, streak, f = evaluate(bank, now=NOW + 4 * WEEK)[0]
    assert (state, streak) == ("COLLECTING", 0) and "the model missed more fraud than the rules" in f["missing"]


def test_a_model_that_loses_by_one_fraud_stays_collecting(bank):
    seed(bank, weeks=8)
    with db.session() as s:
        week = band_gate.window(NOW, IST, 8)[1]
        s.add(_row(bank, 0, week, "FRAUD", "bank_ops", "T1", "T2"))          # rules caught it, model let it through
    state, streak, f = evaluate(bank)[0]
    assert (state, streak) == ("COLLECTING", 0) and f["model_only_missed"] == 1 and f["rules_only_missed"] == 0


def test_enough_volume_but_simulated_fraud_does_not_count(bank):
    seed(bank, weeks=8, fraud_per_week=8, source="simulator")
    f = evaluate(bank)[0][2]
    assert f["fraud"] == 0 and "needs 50 real fraud reports" in f["missing"]


def test_seven_weeks_of_data_is_not_enough_when_eight_are_asked(bank):
    gate = BandGate(minGenuine=2_000, minFraud=50, minWeeks=8, passingEvaluations=4)
    seed(bank, weeks=7, genuine_per_week=400)
    f = evaluate(bank, policy=_policy(gate=gate))[0][2]
    assert f["weeks"] == 7 and "needs 8 separate weeks of data" in f["missing"]


def test_the_lakh_band_is_held_back_by_one_fraud_the_rules_would_have_stopped(bank):
    seed(bank, band=3, weeks=8, fraud=("T3", "T2"))
    with db.session() as s:
        s.add(_row(bank, 3, band_gate.window(NOW, IST, 8)[1], "FRAUD", "bank_ops", "T1", "T2"))
        for _ in range(5):                                                     # the model wins overall
            s.add(_row(bank, 3, band_gate.window(NOW, IST, 8)[1], "FRAUD", "bank_ops", "T2", "T1"))
    f = evaluate(bank)[3][2]
    assert f["missing"] == ["the model let through a fraud the rules would have stopped"]


def test_bands_are_graded_separately(bank):
    seed(bank, band=0, weeks=8)
    out = evaluate(bank)
    assert out[0][2]["genuine"] == 2_400 and out[1][2]["genuine"] == 0 and out[2][2]["genuine"] == 0 and out[3][2]["genuine"] == 0


def test_another_banks_rows_are_never_graded_for_this_one(bank):
    other = f"tn_{uuid.uuid4().hex[:12]}"
    seed(other, weeks=8)
    assert evaluate(bank)[0][2]["genuine"] == 0


def test_every_change_of_state_is_in_the_audit_log_and_the_chain_still_verifies(bank):
    seed(bank, weeks=16, ahead=8)
    for w in range(4):
        evaluate(bank, now=NOW + w * WEEK)
    with db.session() as s:
        entries = s.execute(select(db.AuditLog).where(db.AuditLog.tenant_id == bank,
                                                      db.AuditLog.action.like("band_gate.%"))).scalars().all()
        assert [(e.action, e.target, e.detail["from"], e.detail["to"]) for e in entries] == \
               [("band_gate.eligible", f"{bank}:0", "COLLECTING", "ELIGIBLE")]
        assert entries[0].actor == "system:band-gate"
    assert audit.verify()["intact"]


def test_a_band_on_model_is_graded_but_not_moved_by_grading(bank):
    with db.session() as s:
        s.add(db.BandGateState(tenant_id=bank, band=2, state="MODEL", streak=9, figures={}, changed_at=NOW))
    assert evaluate(bank)[2][0] == "MODEL"


# ---- the nightly run ----------------------------------------------------------------------------------------------------------------

def test_the_nightly_run_grades_banks_with_the_switch_on_and_only_once_a_night(bank):
    off = f"tn_{uuid.uuid4().hex[:12]}"
    with db.session() as s:
        s.add(db.Tenant(id=off, name="Off", slug=f"o{uuid.uuid4().hex[:8]}", policy=_policy(enabled=False).model_dump(mode="json")))
    with db.session() as s:
        band_gate.run_due(s, now=NOW)
        assert s.get(db.BandGateState, (bank, 0)).evaluated_at == NOW
        assert s.get(db.BandGateState, (off, 0)) is None
    with db.session() as s:
        band_gate.run_due(s, now=NOW + 3600)
        assert s.get(db.BandGateState, (bank, 0)).evaluated_at == NOW          # graded less than 20 h ago: not again
    with db.session() as s:
        band_gate.run_due(s, now=NOW + 21 * 3600)
        assert s.get(db.BandGateState, (bank, 0)).evaluated_at == NOW + 21 * 3600


# ---- what the console shows ------------------------------------------------------------------------------------------------------

def test_the_console_hides_figures_built_from_fewer_than_ten_rows(bank):
    seed(bank, weeks=1, genuine_per_week=9, fraud_per_week=3)
    seed(bank, band=1, weeks=2, genuine_per_week=30, fraud_per_week=6)
    evaluate(bank)
    with db.session() as s:
        v = {b["band"]: b for b in band_gate.view(s, bank, _policy())}
    assert (v[0]["genuine"], v[0]["fraud"]) == ("<10", "<10")
    assert v[0]["fraudCaught"] == {"model": None, "rules": None} and v[0]["falseAlarms"] == {"model": None, "rules": None}
    assert (v[1]["genuine"], v[1]["fraud"]) == (60, 12)
    assert v[1]["fraudCaught"] == {"model": 1.0, "rules": 1.0} and v[1]["falseAlarms"] == {"model": 0.0, "rules": 1.0}
    assert [b["label"] for b in v.values()] == ["₹0–2,000", "₹2,000–10,000", "₹10,000–1 lakh", "₹1 lakh and above"]


def test_the_console_endpoint_needs_a_signed_in_operator():
    from amfa.core.app import create_app
    with TestClient(create_app()) as c:
        assert c.get("/admin/api/band-gates").status_code in (401, 403)
