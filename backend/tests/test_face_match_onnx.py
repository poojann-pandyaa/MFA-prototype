import json
import os

import numpy as np
import cv2
import pytest

import config
from services import face_match_onnx

SAMPLE_PATH = "tests/fixtures/sample_face.jpg"


def _load_sample():
    if not os.path.isfile(face_match_onnx.MODEL_PATH):
        pytest.skip("Run anti_spoofing/export_arcface_onnx.py to generate models/arcface.onnx")

    img = cv2.imread(SAMPLE_PATH)
    if img is None:
        pytest.skip(f"Add {SAMPLE_PATH} first - see tests/fixtures/README.md")

    from services import face_detect

    detection = face_detect.detect(img)
    assert detection.bbox is not None, f"expected a face in {SAMPLE_PATH}"
    return img, detection


def test_embed_matches_deepface_on_the_same_aligned_crop():
    """The serving swap this module performs is onnxruntime-instead-of-
    TensorFlow, so the thing that has to match DeepFace exactly is the
    embedding of a *given* 112x112 crop: same weights, same preprocessing.
    Feeding DeepFace the crop align() produced (detector_backend="skip")
    holds the crop constant and leaves only the serving path and the pixel
    normalization under test - which is the whole point of the check.

    Comparing against DeepFace's *full* pipeline instead would also compare
    two different face detectors' crop geometry: DeepFace's default opencv
    Haar box vs our MediaPipe landmarks. That difference alone moves the
    embedding a long way - DeepFace disagrees with *itself* at cosine 0.87
    on this photo between detector_backend="opencv" and "retinaface" - so
    it cannot be held to a 0.95 bar by any pipeline that doesn't reuse the
    Haar detector. test_deepface_era_stored_embedding_still_verifies below
    covers the end-to-end agreement that actually matters instead.
    """
    img, detection = _load_sample()

    aligned = face_match_onnx.align(img, detection.landmarks_px)
    onnx_embedding = face_match_onnx.embed(aligned)

    from deepface import DeepFace

    deepface_result = DeepFace.represent(
        img_path=aligned, model_name="ArcFace", detector_backend="skip"
    )
    deepface_embedding = np.array(deepface_result[0]["embedding"])

    cosine_sim = np.dot(onnx_embedding, deepface_embedding) / (
        np.linalg.norm(onnx_embedding) * np.linalg.norm(deepface_embedding)
    )
    assert cosine_sim > 0.95, (
        f"ONNX/DeepFace embeddings diverge (cosine_sim={cosine_sim:.3f}) - check "
        f"embed()'s normalization against the DeepFace source quoted in "
        f"services/face_match_onnx.py's docstring"
    )


def test_deepface_era_stored_embedding_still_verifies():
    """End-to-end: an embedding enrolled through the old DeepFace path has
    to keep verifying against this pipeline at the configured threshold,
    otherwise swapping the serving path silently locks enrolled users out.
    """
    img, detection = _load_sample()

    aligned = face_match_onnx.align(img, detection.landmarks_px)
    embedding = face_match_onnx.embed(aligned)

    from deepface import DeepFace

    deepface_result = DeepFace.represent(
        img_path=img, model_name="ArcFace", enforce_detection=True
    )
    stored = json.dumps(deepface_result[0]["embedding"])

    assert face_match_onnx.verify(embedding, stored) is True


def test_align_puts_the_eyes_on_the_arcface_template():
    """Guards the alignment itself: a wrong landmark index (eye *corners*
    instead of iris centres, say) still produces a plausible-looking 112x112
    face crop but at the wrong scale, which quietly degrades matching
    instead of failing. Re-detecting in the aligned crop and checking where
    the eyes landed catches that.
    """
    from services import face_detect

    img, detection = _load_sample()

    aligned = face_match_onnx.align(img, detection.landmarks_px)
    assert aligned.shape == (face_match_onnx.ALIGNED_SIZE, face_match_onnx.ALIGNED_SIZE, 3)

    redetected = face_detect.detect(aligned)
    assert redetected.landmarks_px is not None, "aligned crop no longer looks like a face"

    for ref_row, index in (
        (0, face_match_onnx.IMAGE_LEFT_IRIS_INDEX),
        (1, face_match_onnx.IMAGE_RIGHT_IRIS_INDEX),
    ):
        error = np.linalg.norm(
            redetected.landmarks_px[index] - face_match_onnx.REFERENCE_POINTS[ref_row]
        )
        assert error < 8.0, (
            f"eye {index} landed {error:.1f}px from the ArcFace template position "
            f"{face_match_onnx.REFERENCE_POINTS[ref_row]} in the aligned crop"
        )


def test_verify_matches_embedding_against_itself():
    embedding = np.ones(512, dtype=np.float32)
    stored = json.dumps(embedding.tolist())
    assert face_match_onnx.verify(embedding, stored) is True


def test_verify_rejects_dissimilar_embedding():
    embedding = np.ones(512, dtype=np.float32)
    different = -np.ones(512, dtype=np.float32)
    stored = json.dumps(different.tolist())
    assert face_match_onnx.verify(embedding, stored) is False


def test_verify_rejects_missing_stored_embedding():
    assert face_match_onnx.verify(np.ones(512, dtype=np.float32), "") is False


def test_verify_threshold_is_config_driven(monkeypatch):
    a = np.zeros(512, dtype=np.float32)
    a[0] = 1.0
    b = np.zeros(512, dtype=np.float32)
    b[0], b[1] = 0.8, 0.6  # cosine distance of exactly 0.2 from a
    stored = json.dumps(b.tolist())

    monkeypatch.setattr(config, "FACE_MATCH_THRESHOLD", 0.3)
    assert face_match_onnx.verify(a, stored) is True

    monkeypatch.setattr(config, "FACE_MATCH_THRESHOLD", 0.1)
    assert face_match_onnx.verify(a, stored) is False
