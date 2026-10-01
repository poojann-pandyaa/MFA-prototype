"""
Manual check: confirm face_match_onnx distinguishes two different real
people, not just that it agrees with itself on one photo.
Needs tests/fixtures/sample_face.jpg (you) and a second photo of a
different person saved as tests/fixtures/sample_face_other.jpg.
Run: cd backend && python tests/manual_check_face_match.py
"""
import os
import sys

import numpy as np
import cv2

# Run as a plain script (not under pytest, which gets this from conftest.py),
# so backend/ has to go on the path before the project imports below.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from services import face_detect, face_match_onnx


def _embed(path):
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"Missing {path} - see tests/fixtures/README.md")
    detection = face_detect.detect(img)
    assert detection.bbox is not None, f"no face found in {path}"
    aligned = face_match_onnx.align(img, detection.landmarks_px)
    return face_match_onnx.embed(aligned)


mine = _embed("tests/fixtures/sample_face.jpg")
other = _embed("tests/fixtures/sample_face_other.jpg")

cosine_distance = 1 - np.dot(mine, other) / (np.linalg.norm(mine) * np.linalg.norm(other))
print(f"cosine_distance between two different people: {cosine_distance:.3f}")
print(
    f"FACE_MATCH_THRESHOLD: {config.FACE_MATCH_THRESHOLD} "
    f"(distance should be ABOVE this for different people)"
)
