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


# --- Face match must cover the PAD-live frames, not one largest frame ------
#
# Harness: every frame decodes fine (blank JPEGs); face_detect.detect is
# mocked to return one single-face detection per frame whose bbox[0] and
# landmarks encode the frame index, so the PAD/face-match mocks below can
# tell frames apart. The gesture check is mocked to pass - these tests are
# only about WHICH frames get PAD-gated and identity-matched.

from services import pad_onnx, liveness_challenge, face_match_onnx  # noqa: E402

_N_FRAMES = 8  # >= LIVENESS_MIN_VALID_FRAMES (6); 7/8 live >= 0.8 fraction


def _run_pipeline(bbox_sizes, live, matching, challenge="blink"):
    """bbox_sizes[i]: side length of frame i's (square) bbox.
    live: indices PAD calls live. matching: indices whose embedding matches
    the stored one. Returns (result, indices that were face-matched)."""
    frames = _blank_frames_b64(len(bbox_sizes))
    detections = [
        face_detect.FrameDetection(
            bbox=(i, 0, size, size),
            landmarks_px=np.full((478, 2), float(i), dtype=np.float32),
            multi_face=False,
        )
        for i, size in enumerate(bbox_sizes)
    ]
    matched = []

    def _pad(frame, bbox):
        return (1, 0.99) if bbox[0] in live else (0, 0.99)

    def _align(frame, landmarks_px):
        return int(landmarks_px[0, 0])

    def _embed(idx):
        matched.append(idx)
        return idx

    def _verify(embedding, stored):
        return embedding in matching

    with mock.patch.object(face_detect, "detect", side_effect=detections), \
            mock.patch.object(pad_onnx._predictor, "predict_with_bbox", side_effect=_pad), \
            mock.patch.object(liveness_challenge, "verify_gesture", return_value=True), \
            mock.patch.object(face_match_onnx, "align", side_effect=_align), \
            mock.patch.object(face_match_onnx, "embed", side_effect=_embed), \
            mock.patch.object(face_match_onnx, "verify", side_effect=_verify):
        result = biometrics.verify_liveness_and_identity(frames, challenge, json.dumps([0.0] * 512))
    return result, matched


def test_all_live_frames_match_returns_true():
    sizes = [100] * _N_FRAMES
    result, matched = _run_pipeline(sizes, live=set(range(_N_FRAMES)), matching=set(range(_N_FRAMES)))
    assert result is True
    assert sorted(matched) == list(range(_N_FRAMES))  # every live frame was identity-checked


def test_only_largest_frame_matching_is_not_enough():
    # Splice attack: attacker performs the gesture live with their own face
    # and holds the victim's photo close to the camera for one frame (the
    # largest bbox). Every frame is PAD-live here; only that one frame
    # matches the victim's stored embedding.
    sizes = [100] * _N_FRAMES
    sizes[3] = 300
    result, _ = _run_pipeline(sizes, live=set(range(_N_FRAMES)), matching={3})
    assert result is False


def test_non_live_largest_frame_is_never_used_for_matching():
    # Same splice, but the victim-photo frame fails PAD - and is absorbed by
    # the 80% live-fraction tolerance (7/8 live). It has the largest bbox,
    # yet it must not be the frame (or one of the frames) face-matched.
    sizes = [100] * _N_FRAMES
    sizes[5] = 300
    live = set(range(_N_FRAMES)) - {5}
    result, matched = _run_pipeline(sizes, live=live, matching={5})
    assert result is False
    assert 5 not in matched


def test_non_live_frame_ignored_when_all_live_frames_match():
    # Legit user with one PAD-glitched (but largest) frame: the live frames
    # are all theirs, so verification succeeds without ever matching frame 5.
    sizes = [100] * _N_FRAMES
    sizes[5] = 300
    live = set(range(_N_FRAMES)) - {5}
    result, matched = _run_pipeline(sizes, live=live, matching=live)
    assert result is True
    assert sorted(matched) == sorted(live)


def test_one_live_frame_not_matching_fails():
    # Mixed burst: the largest, first and last frames match, but a live
    # frame in the middle is someone else -> the whole burst fails.
    sizes = [100] * _N_FRAMES
    sizes[0] = 300
    everyone = set(range(_N_FRAMES))
    result, _ = _run_pipeline(sizes, live=everyone, matching=everyone - {4})
    assert result is False


def test_pad_fraction_failure_skips_face_match():
    sizes = [100] * _N_FRAMES
    result, matched = _run_pipeline(sizes, live={0, 1, 2}, matching=set(range(_N_FRAMES)))
    assert result is False
    assert matched == []


# --- Gesture evidence must come only from PAD-live frames ------------------
#
# Uses the REAL liveness_challenge.verify_gesture on synthetic landmarks
# (same geometry as tests/test_liveness_challenge.py). Attack: a few
# spliced frames that PAD rejects (absorbed by the 80% live-fraction
# tolerance) carry the gesture, while the genuine live frames carry the
# identity. Those rejected frames must not count as gesture evidence.

from tests.test_liveness_challenge import _base_landmarks, _closed_eyes  # noqa: E402


def _nose_shifted(dx):
    lm = _base_landmarks()
    lm[1, 0] += dx
    return lm


def _run_real_gesture(landmark_frames, live, challenge):
    """landmark_frames[i]: frame i's 478x2 landmarks. live: PAD-live indices.
    Every frame's embedding matches, so the result hinges on PAD+gesture."""
    frames = _blank_frames_b64(len(landmark_frames))
    detections = [
        face_detect.FrameDetection(bbox=(i, 0, 100, 100), landmarks_px=lm, multi_face=False)
        for i, lm in enumerate(landmark_frames)
    ]

    def _pad(frame, bbox):
        return (1, 0.99) if bbox[0] in live else (0, 0.99)

    with mock.patch.object(face_detect, "detect", side_effect=detections), \
            mock.patch.object(pad_onnx._predictor, "predict_with_bbox", side_effect=_pad), \
            mock.patch.object(face_match_onnx, "align", return_value=None), \
            mock.patch.object(face_match_onnx, "embed", return_value=None), \
            mock.patch.object(face_match_onnx, "verify", return_value=True):
        return biometrics.verify_liveness_and_identity(frames, challenge, json.dumps([0.0] * 512))


def test_non_live_frames_at_start_cannot_forge_a_turn():
    # 2 non-live frames with the nose shifted, then 11 frontal live frames.
    for dx in (30, -30):
        lms = [_nose_shifted(dx), _nose_shifted(dx)] + [_base_landmarks() for _ in range(11)]
        live = set(range(2, 13))
        for challenge in ("turn_left", "turn_right"):
            assert _run_real_gesture(lms, live, challenge) is False, (dx, challenge)


def test_non_live_frames_at_end_cannot_forge_a_turn():
    for dx in (30, -30):
        lms = [_base_landmarks() for _ in range(11)] + [_nose_shifted(dx), _nose_shifted(dx)]
        live = set(range(11))
        for challenge in ("turn_left", "turn_right"):
            assert _run_real_gesture(lms, live, challenge) is False, (dx, challenge)


def test_non_live_closed_eye_frame_cannot_forge_a_blink():
    lms = [_base_landmarks() for _ in range(12)]
    lms[6] = _closed_eyes(_base_landmarks())
    live = set(range(12)) - {6}
    assert _run_real_gesture(lms, live, "blink") is False


def test_genuine_live_turn_still_passes():
    # Nose moves steadily image-right relative to the eyes across 13 live
    # frames (subject's own left, unmirrored), plus a frontal non-live
    # frame in the middle that is simply ignored.
    lms = [_nose_shifted(i * 5) for i in range(13)]
    lms[6] = _base_landmarks()
    live = set(range(13)) - {6}
    assert _run_real_gesture(lms, live, "turn_left") is True
    lms_all_live = [_nose_shifted(i * 5) for i in range(13)]
    assert _run_real_gesture(lms_all_live, set(range(13)), "turn_left") is True
    assert _run_real_gesture(lms_all_live, set(range(13)), "turn_right") is False


def test_genuine_live_blink_still_passes():
    lms = [_base_landmarks() for _ in range(12)]
    lms[5] = _closed_eyes(_base_landmarks())
    lms[6] = _closed_eyes(_base_landmarks())
    assert _run_real_gesture(lms, set(range(12)), "blink") is True


# --- Server-side step-2 diagnostics log -----------------------------------

def test_gesture_failure_logs_stage_and_measured_turn_delta(caplog):
    # Static frontal face asked to turn: gesture fails, and the log says so
    # with the signed measured delta and the threshold.
    lms = [_base_landmarks() for _ in range(13)]
    with caplog.at_level("INFO", logger="mfa.step2"):
        assert _run_real_gesture(lms, set(range(13)), "turn_right") is False
    assert "step2 result=gesture_failed challenge=turn_right live_frames=13" in caplog.text
    assert "turn_delta=+0.000 threshold=0.150" in caplog.text


def test_blink_failure_logs_ear_stats(caplog):
    lms = [_base_landmarks() for _ in range(12)]
    with caplog.at_level("INFO", logger="mfa.step2"):
        assert _run_real_gesture(lms, set(range(12)), "blink") is False
    assert "step2 result=gesture_failed challenge=blink" in caplog.text
    assert "ear_threshold=0.200 open_closed_open=False" in caplog.text


def test_face_mismatch_logs_distance(caplog):
    lms = [_nose_shifted(i * 5) for i in range(13)]  # genuine turn_left
    frames = _blank_frames_b64(13)
    detections = [
        face_detect.FrameDetection(bbox=(i, 0, 100, 100), landmarks_px=lm, multi_face=False)
        for i, lm in enumerate(lms)
    ]
    # Orthogonal embeddings -> cosine distance exactly 1.0 (real verify()).
    with mock.patch.object(face_detect, "detect", side_effect=detections), \
            mock.patch.object(pad_onnx._predictor, "predict_with_bbox", return_value=(1, 0.99)), \
            mock.patch.object(face_match_onnx, "align", return_value=None), \
            mock.patch.object(face_match_onnx, "embed", return_value=np.array([1.0, 1.0, 1.0, 1.0])), \
            caplog.at_level("INFO", logger="mfa.step2"):
        result = biometrics.verify_liveness_and_identity(frames, "turn_left", json.dumps([1.0, -1.0, 1.0, -1.0]))
    assert result is False
    assert "step2 result=face_mismatch challenge=turn_left live_frames=13" in caplog.text
    assert "frames_checked=1/13 distance=1.0 threshold=0.450" in caplog.text
