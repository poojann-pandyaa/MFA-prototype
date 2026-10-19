"""UEBA /factor/enroll and /factor/verify against the real service and a small trained model."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from amfa.common.contract import VerifyRequest
from amfa.factors.base import internal_auth
from amfa.factors.ueba.data.behaviour import shape_profile, shape_sample, typing_profile, typing_sample
from amfa.factors.ueba.features import keystroke as ks

TENANT, USER = "tn_test", "cu_test"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from amfa.factors.ueba import service
    from amfa.factors.ueba.training import train
    # pin into a temp file so the test model never overwrites the real ARTIFACTS.sha256
    pins = tmp_path_factory.mktemp("ueba") / "ARTIFACTS.sha256"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(train, "PIN_FILE", pins)
        mp.setattr(service, "PIN_FILE", pins)
        train.run(n_users=40, n_days=60, n_behaviour_users=20, seed=42)
        service.app.dependency_overrides[internal_auth] = lambda: None
        with TestClient(service.app) as c:
            yield c
        service.app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def person():
    rng = np.random.default_rng(5)
    return {"rng": rng, "typing": typing_profile(rng), "shape": shape_profile(rng)}


def enroll(client, person, user=USER, n=6, keys=None, traces=None):
    rng = person["rng"]
    body = {"tenantId": TENANT, "userId": user, "payload": {
        "keystrokeSamples": keys if keys is not None else
        [typing_sample(person["typing"], rng) for _ in range(n)],
        "shapeTraces": traces if traces is not None else
        [shape_sample(person["shape"], rng) for _ in range(4)]}}
    return client.post("/factor/enroll", json=body).json()


def verify(client, keystroke, trace=None, user=USER, shape_asked=True):
    req = VerifyRequest(tenantId=TENANT, userId=user, sessionId="s", transactionId="t",
                        challenge={"shape": [[0.1, 0.8]]} if shape_asked else {},
                        payload={"keystrokeSample": keystroke, "shapeTrace": trace})
    return client.post("/factor/verify", json=req.model_dump(mode="json")).json()


@pytest.fixture(scope="module")
def enrolled(client, person):
    res = enroll(client, person)
    assert res["enrolled"], res
    return res


def genuine(person, shape=True):
    rng = person["rng"]
    return typing_sample(person["typing"], rng), (shape_sample(person["shape"], rng) if shape else None)


def test_enrollment_uses_five_of_six_typings_and_four_traces(enrolled):
    assert enrolled["quality"]["keystroke"]["samples"] == 5
    assert enrolled["quality"]["shape"]["status"] == "ENROLLED"


def test_phrase_endpoint_describes_the_pad(client):
    body = client.get("/enroll/phrase").json()
    assert body["aspectRatio"] == 2.0 and body["shapeTracesRequired"] == 4
    assert body["repetitions"] == ks.TARGET_SAMPLES


def test_the_enrolled_person_passes_and_someone_else_mostly_does_not(client, person, enrolled):
    own = [verify(client, *genuine(person)) for _ in range(5)]
    assert sum(r["status"] == "PASSED" for r in own) >= 4, own
    rng = np.random.default_rng(99)
    other_t, other_s = typing_profile(rng), shape_profile(rng)
    others = [verify(client, typing_sample(other_t, rng), shape_sample(other_s, rng)) for _ in range(5)]
    assert sum(r["status"] == "PASSED" for r in others) <= 1, others


def test_no_answer_at_all_fails_as_missing(client, person, enrolled):
    res = verify(client, [], None, shape_asked=False)
    assert res["status"] == "FAILED" and res["failureReason"] == "CHALLENGE_MISSING"


def test_half_the_phrase_fails_as_incomplete(client, person, enrolled):
    events, _ = genuine(person, shape=False)
    half = [e for e in events if e["down"] <= events[len(events) // 2]["down"]]
    res = verify(client, half, None, shape_asked=False)
    assert res["status"] == "FAILED"
    assert res["failureReason"] in ("CHALLENGE_INCOMPLETE", "CHALLENGE_MISSING")
    assert res["detail"]["dimensions"]["keystroke"]["status"] == "PARTIAL"


def test_skipping_the_shape_cannot_be_passed_on_typing_alone(client, person, enrolled):
    """The shape was part of the challenge. Leaving it out used to be skipped silently."""
    events, _ = genuine(person, shape=False)
    res = verify(client, events, None, shape_asked=True)
    assert res["status"] == "FAILED"
    assert res["failureReason"] == "CHALLENGE_INCOMPLETE"
    assert res["detail"]["dimensions"]["shape"]["status"] == "MISSING"


def test_a_replayed_complete_answer_is_caught(client, person, enrolled):
    events, trace = genuine(person)
    first = verify(client, events, trace)
    assert first["failureReason"] != "REPLAY_IDENTICAL_CAPTURE"
    assert verify(client, events, trace)["status"] == "SPOOF_DETECTED"


def test_incomplete_typings_do_not_enrol(client, person):
    rng = person["rng"]
    short = [typing_sample(person["typing"], rng)[:10] for _ in range(6)]
    res = enroll(client, person, user="cu_short", keys=short)
    assert res["enrolled"] is False and res["reason"] == "NOT_ENOUGH_USABLE_SAMPLES"
    assert res["quality"]["rejected"] == {"PHRASE_INCOMPLETE": 6}


def test_malformed_events_are_a_protocol_failure(client, person, enrolled):
    res = verify(client, [{"key": "t", "down": 0, "up": 99999}], None, shape_asked=False)
    assert res["status"] == "FAILED" and res["failureReason"] == "KEYSTROKE_TIMING_IMPLAUSIBLE"


# ------------------------------------------------------------- touchscreens

def thumbs(profile, rng):
    """A phone keyboard: the key-up arrives about 3 ms after the key-down whatever the finger did."""
    return [{**e, "up": e["down"] + float(rng.uniform(1.5, 4.5))} for e in typing_sample(profile, rng)]


def enroll_input(client, person, user, input, n=6, kinds=None):
    rng = person["rng"]
    typ = thumbs if input == "touch" else typing_sample
    payload = {"input": input,
               "keystrokeSamples": [typ(person["typing"], rng) for _ in range(n)],
               "shapeTraces": [shape_sample(person["shape"], rng) for _ in range(4)]}
    payload["shapePointerTypes"] = kinds if kinds is not None else (["touch"] * 4 if input == "touch" else ["mouse"] * 4)
    return client.post("/factor/enroll", json={"tenantId": TENANT, "userId": user, "payload": payload}).json()


def verify_input(client, user, input, keys, trace, typed_with=None, drawn_with=None):
    payload = {"keystrokeSample": keys, "shapeTrace": trace}
    if typed_with:
        payload["keystrokeInput"] = typed_with
    if drawn_with:
        payload["shapePointerType"] = drawn_with
    req = VerifyRequest(tenantId=TENANT, userId=user, sessionId="s", transactionId="t",
                        challenge={"shape": [[0.1, 0.8]], "input": input}, payload=payload)
    return client.post("/factor/verify", json=req.model_dump(mode="json")).json()


@pytest.fixture(scope="module")
def thumb():
    rng = np.random.default_rng(23)
    return {"rng": rng, "typing": typing_profile(rng), "shape": shape_profile(rng)}


def test_a_phone_enrolment_is_not_mistaken_for_automation(client, thumb):
    """Hold times are ~3 ms on every phone; the bot check looks at the gaps there instead."""
    res = enroll_input(client, thumb, "cu_phone1", "touch")
    assert res["enrolled"], res
    assert res["quality"]["keystroke"]["compares"] == ["interval"]
    assert res["quality"]["enrolledInputs"] == ["touch"]


def test_adding_a_phone_keeps_the_computer_templates(client, thumb):
    assert enroll_input(client, thumb, "cu_both", "keyboard")["enrolled"]
    res = enroll_input(client, thumb, "cu_both", "touch")
    assert set(res["quality"]["enrolledInputs"]) == {"keyboard", "touch"}
    assert res["quality"]["inputs"]["keyboard"] == {"keystroke": True, "shape": True}
    rng = thumb["rng"]
    kb = verify_input(client, "cu_both", "keyboard", typing_sample(thumb["typing"], rng),
                      shape_sample(thumb["shape"], rng), "keyboard", "mouse")
    assert kb["status"] == "PASSED" and kb["detail"]["input"] == "keyboard"


def test_the_genuine_thumb_typist_passes_on_provisional_calibration(client, thumb):
    enroll_input(client, thumb, "cu_phone2", "touch")
    rng = thumb["rng"]
    j = verify_input(client, "cu_phone2", "touch", thumbs(thumb["typing"], rng),
                     shape_sample(thumb["shape"], rng), "touch", "touch")
    assert j["status"] == "PASSED", j
    assert j["detail"]["provisional"] is True
    assert j["detail"]["dimensions"]["keystroke"]["calibration"] == "keystroke"


def test_no_phone_templates_is_insufficient_data_never_a_pass(client, person, enrolled):
    rng = person["rng"]
    j = verify_input(client, USER, "touch", thumbs(person["typing"], rng),
                     shape_sample(person["shape"], rng), "touch", "touch")
    assert j["status"] == "INSUFFICIENT_DATA" and j["failureReason"] == "NO_ENROLLED_TEMPLATE"
    assert j["score"] is None and j["detail"]["enrolledInputs"] == ["keyboard"]


@pytest.mark.parametrize("typed_with,drawn_with", [("keyboard", "touch"), ("touch", "mouse"), ("touch", "pen")])
def test_answering_with_the_other_input_fails(client, thumb, typed_with, drawn_with):
    enroll_input(client, thumb, "cu_phone3", "touch")
    rng = thumb["rng"]
    j = verify_input(client, "cu_phone3", "touch", thumbs(thumb["typing"], rng),
                     shape_sample(thumb["shape"], rng), typed_with, drawn_with)
    assert j["status"] == "FAILED" and j["failureReason"] == "INPUT_MISMATCH"


def test_a_finger_trace_cannot_answer_a_computer_challenge(client, person, enrolled):
    rng = person["rng"]
    k, t = genuine(person)
    j = verify_input(client, USER, "keyboard", k, t, drawn_with="touch")
    assert j["status"] == "FAILED" and j["failureReason"] == "INPUT_MISMATCH"


def test_a_metronome_on_a_phone_is_still_caught(client, thumb):
    enroll_input(client, thumb, "cu_phone4", "touch")
    bot = [{"key": ch, "down": 1000.0 + 150 * i, "up": 1003.0 + 150 * i} for i, ch in enumerate(ks.ENROLL_PHRASE)]
    rng = thumb["rng"]
    j = verify_input(client, "cu_phone4", "touch", bot, shape_sample(thumb["shape"], rng), "touch", "touch")
    assert j["status"] == "FAILED" and j["failureReason"] == "KEYSTROKE_AUTOMATION_SUSPECTED"


def test_a_phone_enrolment_refuses_mouse_traces(client, thumb):
    res = enroll_input(client, thumb, "cu_phone5", "touch", kinds=["touch", "touch", "mouse", "touch"])
    assert not res["enrolled"] and res["reason"] == "SHAPE_POINTER_MISMATCH"
    res = enroll_input(client, thumb, "cu_phone5", "touch", kinds=[])
    assert not res["enrolled"] and res["reason"] == "SHAPE_POINTER_TYPES_INVALID"


def test_shadow_mode_scores_phones_without_deciding(client, thumb, monkeypatch):
    from amfa.factors.ueba import service
    monkeypatch.setattr(service, "TOUCH_MODE", "shadow")
    enroll_input(client, thumb, "cu_phone6", "touch")
    rng = thumb["rng"]
    j = verify_input(client, "cu_phone6", "touch", thumbs(thumb["typing"], rng),
                     shape_sample(thumb["shape"], rng), "touch", "touch")
    assert j["status"] == "INSUFFICIENT_DATA" and j["failureReason"] == "TOUCH_NOT_VALIDATED"
    assert j["score"] is None and j["detail"]["shadowScore"] is not None
