"""Touch input: separate template sets, input checks, shadow mode, migration."""
import os
import sqlite3

import numpy as np
import pytest

os.environ["DB_PATH"] = "/tmp/test_contract.db"

from data.behaviour import shape_profile, shape_sample, typing_profile, typing_sample  # noqa: E402
from features import keystroke as ks  # noqa: E402


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.service import app
    if os.path.exists("/tmp/test_contract.db"):
        os.remove("/tmp/test_contract.db")
    c = TestClient(app)
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def P():
    rng = np.random.default_rng(11)
    return {"rng": rng, "a": typing_profile(rng), "b": typing_profile(rng),
            "sa": shape_profile(rng), "sb": shape_profile(rng)}


def thumbs(profile, rng):
    """A phone keyboard: the key-up arrives almost with the key-down,
    whatever the finger did, so hold time is noise."""
    ev = typing_sample(profile, rng)
    return [{**e, "up": e["down"] + float(rng.uniform(0, 6))} for e in ev]


def enroll(c, P, uid="alice", input="keyboard", who="a", shape="sa"):
    rng = P["rng"]
    typ = thumbs if input == "touch" else typing_sample
    body = {"userId": uid, "input": input,
            "keystrokeSamples": [typ(P[who], rng) for _ in range(6)],
            "shapeTraces": [shape_sample(P[shape], rng) for _ in range(4)]}
    if input == "touch":
        body["shapePointerTypes"] = ["touch"] * 4
    r = c.post("/enroll", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def verify(c, uid="alice", input="keyboard", keys=None, trace=None,
           typed_with=None, drawn_with=None, tester=None):
    cid = c.get("/factor/challenge", params={"userId": uid, "input": input}).json()["challengeId"]
    payload = {"keystroke": keys or [], "shapeTrace": trace or []}
    if typed_with:
        payload["keystrokeInput"] = typed_with
    if drawn_with:
        payload["shapePointerType"] = drawn_with
    body = {"sessionId": "s", "userId": uid, "transactionId": "t",
            "challengeId": cid, "payload": payload}
    if tester:
        body["testerId"] = tester
    return c.post("/factor/verify", json=body).json()


# ------------------------------------------------------------ features

def test_touch_template_compares_only_press_to_press():
    rng = np.random.default_rng(3)
    p = typing_profile(rng)
    tpl = ks.build_template([ks.extract(thumbs(p, rng)) for _ in range(6)], input="touch")
    assert tpl["fields"] == ["interval"]
    s = ks.extract(thumbs(p, rng))
    d = ks.distance(tpl, s)
    # Changing hold times leaves a touch distance unchanged.
    held = {**s, "dwell": s["dwell"] + 200.0, "flight": s["flight"] - 200.0}
    assert ks.distance(tpl, held) == pytest.approx(d)


def test_old_templates_without_fields_are_keyboard():
    rng = np.random.default_rng(4)
    p = typing_profile(rng)
    tpl = ks.build_template([ks.extract(typing_sample(p, rng)) for _ in range(6)])
    old = {k: v for k, v in tpl.items() if k not in ("fields", "input",
                                                      "interval_mean", "interval_std")}
    s = ks.extract(typing_sample(p, rng))
    assert ks.distance(old, s) == pytest.approx(ks.distance(tpl, s))


def test_unidentified_keys_match_nothing():
    """What a phone keyboard sends without the page fix: coverage 0."""
    ev = [{"key": "Unidentified", "down": i * 150.0, "up": i * 150.0 + 3}
          for i in range(len(ks.ENROLL_PHRASE))]
    assert ks.extract(ev)["coverage"] == 0.0


# ----------------------------------------------------------- enrolment

def test_touch_enrolment_keeps_keyboard_templates(client, P):
    enroll(client, P)
    r = enroll(client, P, input="touch")
    assert set(r["enrolledInputs"]) == {"keyboard", "touch"}
    assert r["keystroke"]["compares"] == ["interval"]
    u = client.get("/user/alice").json()
    assert u["inputs"]["keyboard"] == {"keystroke": True, "shape": True}
    assert u["inputs"]["touch"] == {"keystroke": True, "shape": True}


def test_touch_enrolment_rejects_mouse_or_pen_traces(client, P):
    rng = P["rng"]
    base = {"userId": "x", "input": "touch",
            "keystrokeSamples": [thumbs(P["a"], rng) for _ in range(6)],
            "shapeTraces": [shape_sample(P["sa"], rng) for _ in range(4)]}
    for kinds in (["touch", "touch", "mouse", "touch"], ["pen"] * 4, []):
        r = client.post("/enroll", json={**base, "shapePointerTypes": kinds})
        assert r.status_code == 400


def test_keyboard_enrolment_rejects_finger_traces(client, P):
    rng = P["rng"]
    r = client.post("/enroll", json={"userId": "x",
        "keystrokeSamples": [typing_sample(P["a"], rng) for _ in range(6)],
        "shapeTraces": [shape_sample(P["sa"], rng) for _ in range(4)],
        "shapePointerTypes": ["touch"] * 4})
    assert r.status_code == 400


# ---------------------------------------------------------- verification

def test_challenge_says_whether_the_input_is_enrolled(client, P):
    enroll(client, P)
    j = client.get("/factor/challenge", params={"userId": "alice", "input": "touch"}).json()
    assert j["input"] == "touch" and j["inputEnrolled"] is False
    assert j["enrolledInputs"] == ["keyboard"]


def test_missing_input_set_is_insufficient_data_never_a_pass(client, P):
    enroll(client, P)
    rng = P["rng"]
    j = verify(client, input="touch", keys=thumbs(P["a"], rng),
               trace=shape_sample(P["sa"], rng), typed_with="touch", drawn_with="touch")
    assert j["status"] == "INSUFFICIENT_DATA" and j["failureReason"] == "NO_TEMPLATE"
    assert j["score"] is None


def test_genuine_touch_passes(client, P):
    enroll(client, P, input="touch")
    rng = P["rng"]
    j = verify(client, input="touch", keys=thumbs(P["a"], rng),
               trace=shape_sample(P["sa"], rng), typed_with="touch", drawn_with="touch")
    assert j["status"] == "PASSED", j
    assert j["detail"]["input"] == "touch"
    # No real touch measurements fitted yet: borrowed keyboard calibration.
    assert j["detail"]["keystroke"]["provisional"] is True
    assert j["detail"]["provisional"] is True


def test_touch_impostor_scores_lower(client, P):
    enroll(client, P, input="touch")
    rng = P["rng"]
    g = verify(client, input="touch", keys=thumbs(P["a"], rng),
               trace=shape_sample(P["sa"], rng), typed_with="touch", drawn_with="touch")
    i = verify(client, input="touch", keys=thumbs(P["b"], rng),
               trace=shape_sample(P["sb"], rng), typed_with="touch", drawn_with="touch")
    assert i["score"] < g["score"]


def test_keyboard_answer_to_touch_challenge_fails(client, P):
    """Claiming one input and answering with another is not a way round
    either template set."""
    enroll(client, P)
    enroll(client, P, input="touch")
    rng = P["rng"]
    j = verify(client, input="touch", keys=typing_sample(P["a"], rng),
               trace=shape_sample(P["sa"], rng), typed_with="keyboard", drawn_with="touch")
    assert j["status"] == "FAILED" and j["failureReason"] == "INPUT_MISMATCH"
    assert j["detail"]["keystroke"]["inputMismatch"] is True


def test_finger_trace_on_keyboard_challenge_fails(client, P):
    enroll(client, P)
    rng = P["rng"]
    j = verify(client, keys=typing_sample(P["a"], rng),
               trace=shape_sample(P["sa"], rng), drawn_with="touch")
    assert j["status"] == "FAILED" and j["failureReason"] == "INPUT_MISMATCH"


def test_pen_trace_is_refused(client, P):
    enroll(client, P, input="touch")
    rng = P["rng"]
    j = verify(client, input="touch", keys=thumbs(P["a"], rng),
               trace=shape_sample(P["sa"], rng), typed_with="touch", drawn_with="pen")
    assert j["status"] == "FAILED" and j["failureReason"] == "INPUT_MISMATCH"


def test_keyboard_unchanged_for_old_clients(client, P):
    """No input fields at all: keyboard, as before."""
    enroll(client, P)
    rng = P["rng"]
    j = verify(client, keys=typing_sample(P["a"], rng), trace=shape_sample(P["sa"], rng))
    assert j["status"] == "PASSED" and j["detail"]["input"] == "keyboard"
    assert j["detail"]["provisional"] is False


def test_shadow_mode_records_but_does_not_decide(client, P, monkeypatch):
    import api.service as svc
    monkeypatch.setattr(svc, "TOUCH_MODE", "shadow")
    enroll(client, P, input="touch")
    rng = P["rng"]
    j = verify(client, input="touch", keys=thumbs(P["a"], rng),
               trace=shape_sample(P["sa"], rng), typed_with="touch", drawn_with="touch")
    assert j["status"] == "INSUFFICIENT_DATA" and j["failureReason"] == "NOT_VALIDATED"
    assert j["score"] is None
    h = client.get("/history", params={"userId": "alice", "limit": 1}).json()["verifications"][0]
    assert h["dimensions"]["shadowScore"] is not None


def test_tester_id_is_recorded_for_measurement(client, P):
    enroll(client, P)
    rng = P["rng"]
    verify(client, keys=typing_sample(P["b"], rng), trace=shape_sample(P["sb"], rng), tester="bob")
    h = client.get("/history", params={"userId": "alice", "limit": 1}).json()["verifications"][0]
    assert h["dimensions"]["testerId"] == "bob"


# ------------------------------------------------------------- migration

def test_database_from_before_touch_support_still_works(tmp_path):
    from store import db
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE user (user_id TEXT PRIMARY KEY, enrolled_at REAL NOT NULL,
            keystroke_tpl TEXT, mouse_tpl TEXT, shape_tpl TEXT, session_prof TEXT);
        CREATE TABLE challenge (challenge_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            issued_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL);
        INSERT INTO user VALUES ('old', 1.0, '{"n_samples": 5}', NULL, NULL, NULL);
    """)
    c.commit()
    c.close()
    db.init(path)
    u = db.get_user("old", path)
    assert db.enrolled_inputs(u) == ["keyboard"]
    db.issue_challenge("ch_1", "old", 60, path, input="touch")
    assert db.challenge_input("ch_1", path) == "touch"


# ----------------------------------------------------------- measurement

def test_measure_separates_genuine_from_impostor_and_fits(client, P, tmp_path, capsys):
    from scripts import measure
    from scoring.combine import CalibrationSet
    from api import dimensions as dm
    rng = P["rng"]
    enroll(client, P, uid="alice", input="touch", who="a", shape="sa")
    enroll(client, P, uid="bob", input="touch", who="b", shape="sb")
    for _ in range(6):
        for uid, me, other in (("alice", ("a", "sa"), ("b", "sb")), ("bob", ("b", "sb"), ("a", "sa"))):
            verify(client, uid=uid, input="touch", keys=thumbs(P[me[0]], rng),
                   trace=shape_sample(P[me[1]], rng), typed_with="touch", drawn_with="touch",
                   tester=uid)
            verify(client, uid=uid, input="touch", keys=thumbs(P[other[0]], rng),
                   trace=shape_sample(P[other[1]], rng), typed_with="touch", drawn_with="touch",
                   tester="mallory")
    rows = measure.load("/tmp/test_contract.db")
    assert len(rows) == 24 and sum(r["genuine"] for r in rows) == 12
    measure.report(rows, "touch", 0.7)
    out = capsys.readouterr().out
    assert "12 genuine, 12 impostor" in out
    measure.fit(rows, ["touch"], tmp_path)
    cs = CalibrationSet.load(tmp_path)
    assert {"keystroke_touch", "shape_touch"} <= set(cs.calibrators)
    assert dm.calibration_key(cs, "keystroke", "touch") == ("keystroke_touch", False)


def test_touch_spread_has_a_floor_relative_to_the_interval():
    """Five near-identical samples must not make the template intolerant."""
    rng = np.random.default_rng(8)
    p = typing_profile(rng)
    base = thumbs(p, rng)
    same = [[{**e} for e in base] for _ in range(6)]
    tpl = ks.build_template([ks.extract(s) for s in same], input="touch")
    for m, sd in zip(tpl["interval_mean"], tpl["interval_std"]):
        if m is not None:
            assert sd >= ks.TOUCH_STD_FRACTION * abs(m) - 1e-9
    key = ks.build_template([ks.extract(s) for s in same])
    assert min(v for v in key["flight_std"] if v is not None) == ks.STD_FLOOR_MS
