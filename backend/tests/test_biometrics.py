import base64
import json
from unittest import mock

import cv2
import numpy as np
import pytest

from services import biometrics, face_detect


def _blank_frames_b64(n):
    blank = np.zeros((200, 200, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    b64 = base64.b64encode(buf).decode("utf-8")
    return [b64] * n


def test_too_few_valid_frames_raises_retake_needed():
    frames = _blank_frames_b64(3)  # below LIVENESS_MIN_VALID_FRAMES=6, and blank = no face anyway
    with pytest.raises(biometrics.RetakeNeededError):
        biometrics.verify_liveness_and_identity(frames, "blink", json.dumps([0.0] * 512))


def test_short_images_list_raises_retake_needed_not_crash():
    frames = _blank_frames_b64(1)
    with pytest.raises(biometrics.RetakeNeededError):
        biometrics.verify_liveness_and_identity(frames, "blink", json.dumps([0.0] * 512))


def test_extract_embedding_handles_blank_image_gracefully():
    blank_b64 = _blank_frames_b64(1)[0]
    # No face in a blank frame -> empty string, same contract as before.
    assert biometrics.extract_embedding(blank_b64) == ""


def test_extract_embedding_returns_real_embedding_for_real_face():
    # /enroll's embedding extraction moved off DeepFace onto the ONNX path
    # in this task - confirm it still produces a usable embedding for a
    # real face, not just that it fails gracefully on blank input above.
    img = cv2.imread("tests/fixtures/sample_face.jpg")
    if img is None:
        pytest.skip("Add tests/fixtures/sample_face.jpg first - see tests/fixtures/README.md")
    ok, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf).decode("utf-8")

    result = biometrics.extract_embedding(b64)
    assert result != ""
    embedding = json.loads(result)
    assert len(embedding) == 512


def test_multi_face_frame_fails_generic_not_retake():
    # A multi-face frame is a possible attack signal (someone else in
    # frame, or a photo held in front of the attacker's own face) - it
    # must fail the same generic way PAD/gesture/face-match do, NOT take
    # the benign "please retake" path a simple no-face frame would.
    frames = _blank_frames_b64(8)
    fake_detection = face_detect.FrameDetection(
        bbox=(10, 10, 50, 50),
        landmarks_px=np.zeros((478, 2), dtype=np.float32),
        multi_face=True,
    )
    with mock.patch.object(face_detect, "detect", return_value=fake_detection):
        result = biometrics.verify_liveness_and_identity(frames, "blink", json.dumps([0.0] * 512))
    assert result is False
