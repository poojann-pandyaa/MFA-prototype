"""
Single server-side face detector + landmark source for the whole app -
PAD cropping, gesture verification, and ArcFace alignment all consume
this module's output instead of each running their own detector (see
"Unified detection" in docs/superpowers/specs/2026-10-01-active-liveness-challenge-design.md).
"""
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python import core as mp_core

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(CURRENT_DIR, "..", "models", "face_landmarker.task")


@dataclass
class FrameDetection:
    bbox: Optional[Tuple[int, int, int, int]]
    landmarks_px: Optional[np.ndarray]
    multi_face: bool


def _build_landmarker():
    base_options = mp_core.base_options.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.IMAGE,
        num_faces=2,  # >1 lets us detect (and reject) multi-face frames
    )
    return mp_vision.FaceLandmarker.create_from_options(options)


# One landmarker shared by every request. FastAPI runs these sync routes on
# a threadpool, so detect() can be called from several threads at once; that
# is safe only because the pinned mediapipe (0.10.35) FaceLandmarker
# serializes its native calls internally via a SerialDispatcher. Re-check
# this (or add a lock / per-thread landmarker) if the mediapipe pin moves.
_landmarker = _build_landmarker()


def detect(frame_bgr: np.ndarray) -> FrameDetection:
    height, width = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = _landmarker.detect(mp_image)

    faces = result.face_landmarks
    if not faces:
        return FrameDetection(bbox=None, landmarks_px=None, multi_face=False)

    multi_face = len(faces) > 1
    landmarks_px = np.array([[lm.x * width, lm.y * height] for lm in faces[0]], dtype=np.float32)

    xs, ys = landmarks_px[:, 0], landmarks_px[:, 1]
    left, right = int(max(xs.min(), 0)), int(min(xs.max(), width - 1))
    top, bottom = int(max(ys.min(), 0)), int(min(ys.max(), height - 1))
    bbox = (left, top, right - left + 1, bottom - top + 1)

    return FrameDetection(bbox=bbox, landmarks_px=landmarks_px, multi_face=multi_face)
