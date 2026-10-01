import json
import os

import numpy as np
import cv2
import pytest

import config
from services import face_match_onnx

SAMPLE_PATH = "tests/fixtures/sample_face.jpg"


def _load_sample():
    """Needs only a photo - align() and the landmark geometry don't touch the
    ONNX model, so tests that stop there stay runnable before anyone has run
    the export script."""
    img = cv2.imread(SAMPLE_PATH)
    if img is None:
        pytest.skip(f"Add {SAMPLE_PATH} first - see tests/fixtures/README.md")

    from services import face_detect

    detection = face_detect.detect(img)
    assert detection.bbox is not None, f"expected a face in {SAMPLE_PATH}"
    return img, detection


def _require_model():
    if not os.path.isfile(face_match_onnx.MODEL_PATH):
        pytest.skip(
            "Run anti_spoofing/export_arcface_onnx.py to generate models/arcface/arcface.onnx"
        )


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
    _require_model()
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
    # Same weights and same input tensor, so the only difference is float
    # arithmetic in two runtimes: this comes out at 1.000000 and anything that
    # genuinely drifts (a bad re-export, an opset change, a different resize)
    # breaks it far below 0.9999. A 0.95 bar here would have had six orders of
    # magnitude of slack and caught none of them.
    assert cosine_sim > 0.9999, (
        f"ONNX/DeepFace embeddings diverge (cosine_sim={cosine_sim:.6f}) - check "
        f"embed()'s normalization against the DeepFace source quoted in "
        f"services/face_match_onnx.py's docstring"
    )


def test_deepface_era_stored_embedding_still_verifies():
    """End-to-end: an embedding enrolled through the old DeepFace path has
    to keep verifying against this pipeline at the configured threshold,
    otherwise swapping the serving path silently locks enrolled users out.
    """
    _require_model()
    img, detection = _load_sample()

    aligned = face_match_onnx.align(img, detection.landmarks_px)
    embedding = face_match_onnx.embed(aligned)

    from deepface import DeepFace

    deepface_result = DeepFace.represent(
        img_path=img, model_name="ArcFace", enforce_detection=True
    )
    stored = json.dumps(deepface_result[0]["embedding"])

    assert face_match_onnx.verify(embedding, stored) is True


def test_align_fits_the_arcface_template_closely():
    """Guards the alignment geometry: how well the five chosen points actually
    land on the canonical template once the similarity transform is applied.

    Note what this does and doesn't catch. A broken transform - a bad fit, a
    reflection, a scale error - blows the residual up and fails here. A wrong
    *landmark index* only partly shows up: measured on two real photos, the
    inner-eye-corner variant the plan originally specified comes in at
    6.37/6.94px mean residual against 4.01/4.50px for the iris centres, so the
    bound below catches it, but an outer-corner variant (5.20/4.78px) would
    slip through. The indices are pinned by the RetinaFace cross-check
    documented in face_match_onnx.py, not by this test.
    """
    img, detection = _load_sample()

    aligned = face_match_onnx.align(img, detection.landmarks_px)
    assert aligned.shape == (face_match_onnx.ALIGNED_SIZE, face_match_onnx.ALIGNED_SIZE, 3)

    src = np.array(
        [detection.landmarks_px[i] for i in face_match_onnx.LANDMARK_INDICES],
        dtype=np.float32,
    )
    transform = face_match_onnx._similarity_transform(src, face_match_onnx.REFERENCE_POINTS)
    projected = cv2.transform(src.reshape(-1, 1, 2), transform).reshape(-1, 2)
    residuals = np.linalg.norm(projected - face_match_onnx.REFERENCE_POINTS, axis=1)

    assert residuals.mean() < 5.5, (
        f"alignment fits the ArcFace template poorly - mean residual "
        f"{residuals.mean():.2f}px, per-point {residuals.round(2)} - check "
        f"LANDMARK_INDICES and _similarity_transform"
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
