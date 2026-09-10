"""
Presentation-attack-detection (anti-spoofing) inference via ONNX Runtime.

Replaces the original torch-at-request-time path
(backend/anti_spoofing/src/anti_spoof_predict.py, which also reloaded the
model's state dict from disk on *every single call*). This module:
  - loads each model once at import time instead of per-request
  - has no torch import anywhere in the request path -> smaller install,
    faster cold start, lower RAM, and the same .onnx artifact this module
    loads is what the web (onnxruntime-web) and Android (onnxruntime
    mobile) clients can eventually run too.

The weights themselves are unchanged: they're the same pretrained
MiniFASNet checkpoints the project already vendored, exported to ONNX by
backend/anti_spoofing/export_onnx.py (see that file for how to regenerate
them, or ml/export.py once a custom-trained model replaces them).
"""
import math
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ANTI_SPOOFING_DIR = os.path.join(CURRENT_DIR, "..", "anti_spoofing")
MODEL_DIR = os.path.join(CURRENT_DIR, "..", "models")

if ANTI_SPOOFING_DIR not in sys.path:
    sys.path.append(ANTI_SPOOFING_DIR)

# Both of these modules are pure cv2/numpy/stdlib - no torch import, so
# pulling them in doesn't drag PyTorch into the serving process.
from src.generate_patches import CropImage
from src.utility import parse_model_name


class _FaceDetector:
    """RetinaFace (Caffe) bounding-box detector - unchanged from upstream."""

    def __init__(self):
        caffemodel = os.path.join(ANTI_SPOOFING_DIR, "resources", "detection_model", "Widerface-RetinaFace.caffemodel")
        deploy = os.path.join(ANTI_SPOOFING_DIR, "resources", "detection_model", "deploy.prototxt")
        self.net = cv2.dnn.readNetFromCaffe(deploy, caffemodel)

    def get_bbox(self, img):
        height, width = img.shape[0], img.shape[1]
        aspect_ratio = width / height
        if width * height >= 192 * 192:
            img = cv2.resize(
                img,
                (int(192 * math.sqrt(aspect_ratio)), int(192 / math.sqrt(aspect_ratio))),
                interpolation=cv2.INTER_LINEAR,
            )
        blob = cv2.dnn.blobFromImage(img, 1, mean=(104, 117, 123))
        self.net.setInput(blob, "data")
        out = self.net.forward("detection_out").squeeze()
        if out.ndim != 2 or out.shape[0] == 0:
            return None
        max_conf_index = int(np.argmax(out[:, 2]))
        if out[max_conf_index, 2] < 0.6:
            return None
        left, top, right, bottom = (
            out[max_conf_index, 3] * width,
            out[max_conf_index, 4] * height,
            out[max_conf_index, 5] * width,
            out[max_conf_index, 6] * height,
        )
        return [int(left), int(top), int(right - left + 1), int(bottom - top + 1)]


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / np.sum(e, axis=-1, keepdims=True)


class _PadModel:
    def __init__(self, onnx_path: str):
        filename = os.path.basename(onnx_path)
        pth_style_name = filename.replace(".onnx", ".pth")
        self.h_input, self.w_input, self.model_type, self.scale = parse_model_name(pth_style_name)
        self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def predict(self, face_crop_bgr: np.ndarray) -> np.ndarray:
        # HWC uint8 BGR [0,255] -> CHW float32 [0,1], batch dim - matches
        # the original ToTensor() preprocessing exactly (no mean/std norm).
        img = face_crop_bgr.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))[np.newaxis, ...]
        logits = self.session.run(None, {self.input_name: img})[0]
        return _softmax(logits)


class AntiSpoofPredictor:
    def __init__(self, model_dir: str = MODEL_DIR):
        self.detector = _FaceDetector()
        self.cropper = CropImage()
        self.models = []
        if os.path.isdir(model_dir):
            for f in sorted(os.listdir(model_dir)):
                if f.endswith(".onnx"):
                    self.models.append(_PadModel(os.path.join(model_dir, f)))

    @property
    def ready(self) -> bool:
        return len(self.models) > 0

    def predict(self, img_bgr: np.ndarray):
        """Returns (label, confidence, bbox) or (None, None, None) if no face found."""
        if not self.ready:
            return None, None, None

        bbox = self.detector.get_bbox(img_bgr)
        if bbox is None:
            return None, None, None

        prediction = np.zeros((1, 3), dtype=np.float32)
        for model in self.models:
            crop = self.cropper.crop(
                org_img=img_bgr,
                bbox=bbox,
                scale=model.scale,
                out_w=model.w_input,
                out_h=model.h_input,
                crop=model.scale is not None,
            )
            prediction += model.predict(crop)

        label = int(np.argmax(prediction))
        confidence = float(prediction[0][label] / len(self.models))
        return label, confidence, bbox


# Loaded once at process start, reused across all requests.
try:
    _predictor = AntiSpoofPredictor()
    if not _predictor.ready:
        print(f"Warning: no ONNX anti-spoofing models found in {MODEL_DIR}. "
              f"Run backend/anti_spoofing/export_onnx.py first.")
except Exception as e:
    print(f"Warning: failed to initialize ONNX anti-spoofing predictor: {e}")
    _predictor = None


def check_liveness(img_bgr: np.ndarray, threshold: float) -> bool:
    """1 == real/live face label in the underlying MiniFASNet 3-class scheme."""
    if _predictor is None or not _predictor.ready:
        print("Anti-spoofing model not initialized properly.")
        return False

    label, confidence, _ = _predictor.predict(img_bgr)
    if label is None:
        return False

    print(f"Liveness label: {label}, confidence: {confidence:.3f}")
    return label == 1 and confidence > threshold
