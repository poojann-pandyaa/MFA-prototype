"""
ArcFace alignment + embedding + matching via ONNX Runtime - replaces
DeepFace at request time (see backend/anti_spoofing/export_arcface_onnx.py
for how arcface.onnx is produced; same weights, different serving path).

Preprocessing below is DeepFace's own, read off the installed package
(deepface 0.0.100) rather than assumed:

  - deepface/models/facial_recognition/ArcFace.py - `input_shape = (112, 112)`,
    `Input(shape=(112, 112, 3))`, so NHWC, and a 512-d embedding.
  - deepface/modules/representation.py:170-186 - `extract_faces()` hands back
    the crop in RGB, `represent()` immediately flips it to **BGR**
    (`img = img[:, :, ::-1]`, commented "rgb to bgr"), resizes it, then calls
    `normalize_input(img, normalization)` with `normalization="base"`, its
    default.
  - deepface/modules/preprocessing.py:119-121 - `resize_image()` finishes with
    `img = (img.astype(np.float32) / 255.0)`; `normalize_input()` returns the
    image untouched for `"base"`.

So the tensor the ArcFace graph actually sees on DeepFace's default path is
112x112, **BGR**, float32, scaled to [0, 1] - nothing else. Note the
`normalization == "ArcFace"` branch in that same preprocessing module
((x - 127.5) / 128, the reference study's normalization) is *not* on
`represent()`'s default path, so it is not what produced this project's
stored embeddings, and using it here would silently invalidate every
existing enrollment. tests/test_face_match_onnx.py pins this numerically.
"""
import json
import os

import cv2
import numpy as np
import onnxruntime as ort

import config

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Its own subdirectory, deliberately not models/ alongside the PAD weights:
# pad_onnx.py's ensemble-fallback branch scans models/ for *every* .onnx file
# and parses a crop scale out of each filename, which arcface.onnx would break.
MODEL_PATH = os.path.join(CURRENT_DIR, "..", "models", "arcface", "arcface.onnx")
ALIGNED_SIZE = 112

# Standard ArcFace alignment reference points (112x112 canonical template,
# the same one insightface's face_align.norm_crop uses), in row order:
# eye on the image's left, eye on the image's right, nose tip, mouth corner
# on the image's left, mouth corner on the image's right.
REFERENCE_POINTS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)

# MediaPipe Face Mesh indices for the same five points, as served by
# services.face_detect (478 landmarks, i.e. the refined set *with* irises).
#
# Named by which side of the *image* they fall on, which is what the template
# above is ordered by - MediaPipe's own naming is relative to the subject, so
# its "left eye" sits on the image's right and mixing the two conventions
# mirrors every crop.
#
# The eyes have to be iris *centres* (468/473), not eye corners: the template
# was built from RetinaFace's 5-point output, whose eye points are pupils.
# Checked against RetinaFace's own landmarks on two real photos - the iris
# centres sit 0.02-0.13 interocular distances from RetinaFace's eye points
# while the inner corners (133/362) sit 0.23-0.26 away.
#
# Getting this wrong does not blow the crop up, because the scale of a
# five-point least-squares fit is anchored by the nose and mouth too: measured,
# the inner-corner variant still lands the irises within ~6% of the template's
# eye separation. It just quietly shifts the framing - enough to move the
# embedding by cosine 0.10, i.e. ~22% of the FACE_MATCH_THRESHOLD budget spent
# on nothing. That is exactly the kind of error no smoke test would surface.
#
# Nose 4 (the tip apex) beat 1, 19, 94 and 2 on template fit residual on both
# photos; mouth corners 61/291 are the outer commissures.
IMAGE_LEFT_IRIS_INDEX = 468
IMAGE_RIGHT_IRIS_INDEX = 473
NOSE_TIP_INDEX = 4
IMAGE_LEFT_MOUTH_INDEX = 61
IMAGE_RIGHT_MOUTH_INDEX = 291

LANDMARK_INDICES = (
    IMAGE_LEFT_IRIS_INDEX,
    IMAGE_RIGHT_IRIS_INDEX,
    NOSE_TIP_INDEX,
    IMAGE_LEFT_MOUTH_INDEX,
    IMAGE_RIGHT_MOUTH_INDEX,
)

def _load_session():
    """Loaded once at process start, reused across all requests (same
    rationale as services/pad_onnx.py). arcface.onnx is too large to commit,
    so a checkout that hasn't run the export script yet won't have it -
    importing this module still has to work in that case, and embed() is
    where it becomes an error."""
    if not os.path.isfile(MODEL_PATH):
        print(
            f"Warning: ArcFace ONNX model not found at {MODEL_PATH}. "
            f"Run backend/anti_spoofing/export_arcface_onnx.py to generate it."
        )
        return None, None
    session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    return session, session.get_inputs()[0].name


_session, _input_name = _load_session()


def _similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity transform (rotation + uniform scale +
    translation) mapping src onto dst - the Umeyama estimate, which is what
    insightface's norm_crop uses via skimage's SimilarityTransform.

    Deliberately not cv2.estimateAffinePartial2D: that fits robustly, by
    sampling subsets at random (RANSAC by default, LMEDS otherwise), so with
    only five points it can discard a good one and it isn't reproducible run
    to run. An identity check has to give the same answer every time, and
    there are no outliers to reject in a fixed five-point set anyway.
    """
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_centered, dst_centered = src - src_mean, dst - dst_mean

    covariance = (dst_centered.T @ src_centered) / len(src)
    u, singular_values, vt = np.linalg.svd(covariance)

    # Reflect if the SVD produced an improper rotation - a mirrored face is
    # never the right answer here.
    correction = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        correction[1, 1] = -1

    rotation = u @ correction @ vt
    scale = (singular_values * np.diag(correction)).sum() / src_centered.var(axis=0).sum()

    transform = np.zeros((2, 3), dtype=np.float32)
    transform[:, :2] = scale * rotation
    transform[:, 2] = dst_mean - scale * rotation @ src_mean
    return transform


def align(frame_bgr: np.ndarray, landmarks_px: np.ndarray) -> np.ndarray:
    """Warp the face in frame_bgr onto the canonical 112x112 ArcFace template,
    using the five landmarks above. Returns a 112x112x3 BGR crop."""
    src = np.array([landmarks_px[i] for i in LANDMARK_INDICES], dtype=np.float32)
    transform = _similarity_transform(src, REFERENCE_POINTS)
    return cv2.warpAffine(
        frame_bgr, transform, (ALIGNED_SIZE, ALIGNED_SIZE), borderValue=0.0
    )


def embed(aligned_crop_bgr: np.ndarray) -> np.ndarray:
    """512-d ArcFace embedding of an aligned crop. BGR in [0, 1] - see the
    module docstring for where that comes from."""
    if _session is None:
        raise RuntimeError(
            f"ArcFace ONNX model missing at {MODEL_PATH} - run "
            f"backend/anti_spoofing/export_arcface_onnx.py to generate it."
        )

    img = aligned_crop_bgr.astype(np.float32) / 255.0
    output = _session.run(None, {_input_name: img[np.newaxis, ...]})[0]
    return output[0]


def verify(embedding: np.ndarray, stored_embedding_str: str) -> bool:
    """Cosine-distance match against a stored (JSON-encoded) embedding.

    Threshold is config-driven rather than hardcoded so it can be
    recalibrated against real data via ml/eval.py - see
    config.FACE_MATCH_THRESHOLD.
    """
    if not stored_embedding_str:
        return False

    stored = np.array(json.loads(stored_embedding_str))
    cosine_distance = 1 - np.dot(stored, embedding) / (
        np.linalg.norm(stored) * np.linalg.norm(embedding)
    )
    return bool(cosine_distance < config.FACE_MATCH_THRESHOLD)
