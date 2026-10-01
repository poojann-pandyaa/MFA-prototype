# backend/tests/test_login_flow.py
import config
import models
from services import auth, biometrics


def _enroll_user(client, username="alice", password="hunter2"):
    response = client.post("/enroll", json={
        "username": username,
        "password": password,
        "face_image_b64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    })
    return response


def _force_high_risk_session(client, db_session, username, device_identifier):
    # calculate_risk_score returns HIGH for any never-before-seen device,
    # which is already true for a freshly enrolled user's first login.
    response = client.post("/login/step1", json={
        "username": username, "password": "hunter2", "device_identifier": device_identifier,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["require_step2"] is True
    assert body["challenge_type"] in ["blink", "turn_left", "turn_right"]
    return body["session_id"], body["challenge_type"]


def test_step1_returns_challenge_type(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, challenge_type = _force_high_risk_session(client, db_session, "alice", "device-x")
    assert session_id
    assert challenge_type


def test_step2_generic_failure_message_same_for_pad_gesture_and_match(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)

    for outcome_name, side_effect in [
        ("pad_fail", False),
        ("gesture_fail", False),
        ("match_fail", False),
    ]:
        session_id, _ = _force_high_risk_session(client, db_session, "alice", f"device-{outcome_name}")
        monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: side_effect)
        response = client.post("/login/step2", json={
            "username": "alice",
            "images": ["irrelevant-because-mocked"],
            "device_identifier": f"device-{outcome_name}",
            "session_id": session_id,
        })
        assert response.status_code == 401
        assert response.json()["detail"] == "Verification failed. Please try again."


def test_step2_retake_needed_returns_400_with_distinct_message(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-retake")

    def _raise_retake(*a, **k):
        raise biometrics.RetakeNeededError("Could not clearly see a single face across enough frames. Please retake.")

    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", _raise_retake)
    response = client.post("/login/step2", json={
        "username": "alice", "images": ["x"], "device_identifier": "device-retake", "session_id": session_id,
    })
    assert response.status_code == 400
    assert "retake" in response.json()["detail"].lower()


def test_step2_success_issues_token(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-ok")
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)

    response = client.post("/login/step2", json={
        "username": "alice", "images": ["x"], "device_identifier": "device-ok", "session_id": session_id,
    })
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_step2_rejects_without_consuming_session_twice(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-replay")
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)

    payload = {"username": "alice", "images": ["x"], "device_identifier": "device-replay", "session_id": session_id}
    first = client.post("/login/step2", json=payload)
    assert first.status_code == 200
    second = client.post("/login/step2", json=payload)
    assert second.status_code == 401


def test_step2_oversized_burst_rejected_before_decoding(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-big")

    def _must_not_run(*a, **k):
        raise AssertionError("verify_liveness_and_identity must not run for an oversized burst")

    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", _must_not_run)
    response = client.post("/login/step2", json={
        "username": "alice",
        "images": ["x"] * (config.LIVENESS_MAX_FRAMES + 1),
        "device_identifier": "device-big",
        "session_id": session_id,
    })
    assert 400 <= response.status_code < 500


def test_step2_malformed_base64_returns_4xx_not_500(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-badb64")

    # Real b64_to_cv2 / verify_liveness_and_identity: "abc" has invalid padding.
    response = client.post("/login/step2", json={
        "username": "alice", "images": ["abc"], "device_identifier": "device-badb64", "session_id": session_id,
    })
    assert response.status_code == 400
    assert "invalid image" in response.json()["detail"].lower()
