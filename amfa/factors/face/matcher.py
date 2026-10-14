from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from amfa.common import artifacts
from amfa.factors.face.gesture import nose_offset

DET_SCORE = 0.80
MIN_FACE_PX = 80

MATCH_THRESHOLD = max(float(os.getenv("AMFA_FACE_MATCH_THRESHOLD", "0.363")), 0.30)


@dataclass
class DetectedFace:
    box: np.ndarray
    score: float
    width: int
    yaw: float
    embedding: Optional[np.ndarray] = None


class FaceMatcher:
    def __init__(self) -> None:
        det = artifacts.verified("face/face_detection_yunet_2023mar.onnx")
        rec = artifacts.verified("face/face_recognition_sface_2021dec.onnx")
        self._det = cv2.FaceDetectorYN.create(str(det), "", (320, 320), DET_SCORE, 0.3, 50)
        self._rec = cv2.FaceRecognizerSF.create(str(rec), "")
        self._lock = threading.Lock()
        self.version = "yunet-2023mar+sface-2021dec"

    def detect(self, img: np.ndarray) -> list[DetectedFace]:
        h, w = img.shape[:2]
        with self._lock:
            self._det.setInputSize((w, h))
            _, faces = self._det.detect(img)
        out = []
        for f in (faces if faces is not None else []):
            lm = f[4:14].reshape(5, 2)
            yaw = nose_offset(lm)
            if yaw is None:
                continue
            out.append(DetectedFace(box=f, score=float(f[14]), width=int(f[2]), yaw=yaw))
        return out

    def embed(self, img: np.ndarray, face: DetectedFace) -> np.ndarray:
        with self._lock:
            aligned = self._rec.alignCrop(img, face.box)
            feat = self._rec.feature(aligned).flatten().astype(np.float32)
        n = float(np.linalg.norm(feat))
        return feat / n if n > 0 else feat


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(a @ b / (na * nb)) if na and nb else 0.0


@lru_cache(maxsize=1)
def get() -> FaceMatcher:
    return FaceMatcher()
