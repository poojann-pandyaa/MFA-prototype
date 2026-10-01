# backend/tests/test_login_flow.py
import config
import models
import schemas
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


_GENERIC_FAILURE = "Verification failed. Please try again."
_SESSION_GONE = "Invalid, expired, or already-used verification session"


def test_step2_unexpected_exception_is_generic_401_and_consumes_session(client, db_session, monkeypatch, caplog):
    # e.g. arcface.onnx missing -> RuntimeError from embed() AFTER PAD and
    # the gesture already passed. A 500 there would tell the client those
    # checks passed; it must look exactly like any other failed attempt.
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-boom")

    def _boom(*a, **k):
        raise RuntimeError("ArcFace ONNX model missing")

    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", _boom)
    payload = {"username": "alice", "images": ["x"], "device_identifier": "device-boom", "session_id": session_id}
    response = client.post("/login/step2", json=payload)
    assert response.status_code == 401
    assert response.json()["detail"] == _GENERIC_FAILURE
    # ...but the real cause is logged server-side, with its traceback.
    assert "Unexpected error during step-2 verification" in caplog.text
    assert "ArcFace ONNX model missing" in caplog.text

    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)
    replay = client.post("/login/step2", json=payload)
    assert replay.status_code == 401
    assert replay.json()["detail"].startswith(_SESSION_GONE)


def test_step2_plain_failure_consumes_session(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-fail")

    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: False)
    payload = {"username": "alice", "images": ["x"], "device_identifier": "device-fail", "session_id": session_id}
    first = client.post("/login/step2", json=payload)
    assert first.status_code == 401
    assert first.json()["detail"] == _GENERIC_FAILURE

    # Even a would-be success on retry can't reuse the burned session.
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)
    replay = client.post("/login/step2", json=payload)
    assert replay.status_code == 401
    assert replay.json()["detail"].startswith(_SESSION_GONE)


def test_step2_oversized_single_image_rejected_4xx(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-hugeimg")

    def _must_not_run(*a, **k):
        raise AssertionError("verify_liveness_and_identity must not run for an oversized image")

    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", _must_not_run)
    response = client.post("/login/step2", json={
        "username": "alice",
        "images": ["A" * (schemas.MAX_IMAGE_B64_CHARS + 1)],
        "device_identifier": "device-hugeimg",
        "session_id": session_id,
    })
    assert 400 <= response.status_code < 500


def test_step2_gesture_failure_logs_server_side_but_body_stays_generic(client, db_session, monkeypatch, caplog):
    # Real verify_liveness_and_identity with detection/PAD mocked: a static
    # frontal face fails every challenge type at the gesture stage. The
    # diagnostic goes to the server log; the HTTP body must not change.
    import base64
    import cv2
    import numpy as np
    from services import face_detect, pad_onnx
    from tests.test_liveness_challenge import _base_landmarks

    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, challenge_type = _force_high_risk_session(client, db_session, "alice", "device-diag")

    detection = face_detect.FrameDetection(bbox=(10, 10, 100, 100), landmarks_px=_base_landmarks(), multi_face=False)
    monkeypatch.setattr(face_detect, "detect", lambda frame: detection)
    monkeypatch.setattr(pad_onnx._predictor, "predict_with_bbox", lambda frame, bbox: (1, 0.99))
    ok, buf = cv2.imencode(".jpg", np.zeros((50, 50, 3), dtype=np.uint8))
    frame_b64 = base64.b64encode(buf).decode("utf-8")

    with caplog.at_level("INFO", logger="mfa.step2"):
        response = client.post("/login/step2", json={
            "username": "alice", "images": [frame_b64] * 13,
            "device_identifier": "device-diag", "session_id": session_id,
        })
    assert response.status_code == 401
    assert response.json() == {"detail": _GENERIC_FAILURE}
    assert f"step2 result=gesture_failed challenge={challenge_type} live_frames=13" in caplog.text


def _make_device_known(client, monkeypatch, username, device_identifier):
    # A passed step 2 is what registers a device; after that, step 1 from the
    # same device is LOW risk (password only) under normal config.
    session_id, _ = _force_high_risk_session(client, None, username, device_identifier)
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)
    response = client.post("/login/step2", json={
        "username": username, "images": ["x"],
        "device_identifier": device_identifier, "session_id": session_id,
    })
    assert response.status_code == 200


def test_known_device_skips_face_check_by_default(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    _make_device_known(client, monkeypatch, "alice", "device-known")

    response = client.post("/login/step1", json={
        "username": "alice", "password": "hunter2", "device_identifier": "device-known",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["require_step2"] is False
    assert body["access_token"]


def test_always_require_face_check_forces_step2_on_known_device(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    _make_device_known(client, monkeypatch, "alice", "device-known")
    monkeypatch.setattr(config, "ALWAYS_REQUIRE_FACE_CHECK", True)

    response = client.post("/login/step1", json={
        "username": "alice", "password": "hunter2", "device_identifier": "device-known",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["require_step2"] is True
    assert "access_token" not in body
    assert body["session_id"]
    assert body["challenge_type"] in config.CHALLENGE_TYPES
