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
