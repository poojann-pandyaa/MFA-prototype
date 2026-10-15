from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from amfa.common import artifacts

LIVE_CLASS = 1
_MODELS = ("face/2.7_80x80_MiniFASNetV2.onnx", "face/4_0_0_80x80_MiniFASNetV1SE.onnx")


def _parse_model_name(filename: str) -> tuple[int, int, Optional[float]]:
    info = filename.split("_")[0:-1]
    h, w = info[-1].split("x")
    scale = None if info[0] == "org" else float(info[0])
    return int(h), int(w), scale


def _crop_box(src_w: int, src_h: int, bbox, scale: float):
    x, y, box_w, box_h = bbox
    scale = min((src_h - 1) / box_h, min((src_w - 1) / box_w, scale))
    new_w, new_h = box_w * scale, box_h * scale
    cx, cy = box_w / 2 + x, box_h / 2 + y
    l, t = cx - new_w / 2, cy - new_h / 2
    r, b = cx + new_w / 2, cy + new_h / 2
    if l < 0:
        r -= l
        l = 0
    if t < 0:
        b -= t
        t = 0
    if r > src_w - 1:
        l -= r - src_w + 1
        r = src_w - 1
    if b > src_h - 1:
        t -= b - src_h + 1
        b = src_h - 1
    return int(l), int(t), int(r), int(b)


def _crop(img, bbox, scale, out_w, out_h):
    if scale is None:
        return cv2.resize(img, (out_w, out_h))
    h, w = img.shape[:2]
    l, t, r, b = _crop_box(w, h, bbox, scale)
    return cv2.resize(img[t:b + 1, l:r + 1], (out_w, out_h))


# The upstream code shrinks to 192x192 worth of pixels, sized for phone selfies where the face fills the
# frame. On a landscape laptop webcam that leaves the face ~55 px and RetinaFace scores it 0.0, so a real
# person reads as "no face, not live". 480 keeps a typical webcam face ~150 px (measured 0.97 confidence).
DETECT_SIDE = 480


def detector_input(img):
    height, width = img.shape[:2]
    if width * height < DETECT_SIDE * DETECT_SIDE:
        return img
    aspect = width / height
    return cv2.resize(img, (int(DETECT_SIDE * math.sqrt(aspect)), int(DETECT_SIDE / math.sqrt(aspect))),
                      interpolation=cv2.INTER_LINEAR)


@dataclass
class PadFrame:
    live_prob: float
    label: int
    bbox: Optional[list[int]]


class FacePAD:
    def __init__(self) -> None:
        import onnxruntime as ort

        proto = artifacts.verified("face/deploy.prototxt")
        weights = artifacts.verified("face/Widerface-RetinaFace.caffemodel")
        self._det = cv2.dnn.readNetFromCaffe(str(proto), str(weights))
        self._det_lock = threading.Lock()
        self._models = []
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        for rel in _MODELS:
            artifacts.verified(rel + ".data")
            path = artifacts.verified(rel)
            h, w, scale = _parse_model_name(path.stem)
            sess = ort.InferenceSession(str(path), sess_options=opts,
                                        providers=["CPUExecutionProvider"])
            self._models.append((sess, sess.get_inputs()[0].name, h, w, scale))
        self.version = "minifasnet-v2+v1se-onnx"

    def _bbox(self, img) -> Optional[list[int]]:
        height, width = img.shape[:2]
        blob = cv2.dnn.blobFromImage(detector_input(img), 1, mean=(104, 117, 123))
        with self._det_lock:
            self._det.setInput(blob, "data")
            out = self._det.forward("detection_out").squeeze()
        if out.ndim != 2 or out.shape[0] == 0:
            return None
        i = int(np.argmax(out[:, 2]))
        if out[i, 2] < 0.6:
            return None
        l, t, r, b = out[i, 3] * width, out[i, 4] * height, out[i, 5] * width, out[i, 6] * height
        return [int(l), int(t), int(r - l + 1), int(b - t + 1)]

    def score(self, img_bgr: np.ndarray) -> PadFrame:
        bbox = self._bbox(img_bgr)
        if bbox is None:
            return PadFrame(live_prob=0.0, label=-1, bbox=None)
        acc = np.zeros(3, dtype=np.float64)
        for sess, name, h, w, scale in self._models:
            crop = _crop(img_bgr, bbox, scale, w, h)
            x = np.ascontiguousarray(crop.astype(np.float32).transpose(2, 0, 1)[None])
            logits = sess.run(None, {name: x})[0][0].astype(np.float64)
            e = np.exp(logits - logits.max())
            acc += e / e.sum()
        probs = acc / len(self._models)
        return PadFrame(live_prob=float(probs[LIVE_CLASS]), label=int(np.argmax(probs)), bbox=bbox)


@lru_cache(maxsize=1)
def get() -> FacePAD:
    return FacePAD()
