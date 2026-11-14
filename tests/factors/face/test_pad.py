from pathlib import Path

import numpy as np

from amfa.common import artifacts
from amfa.factors.face import pad


def test_a_laptop_webcam_face_stays_big_enough_for_the_pad_detector():
    """The PAD crop detector (RetinaFace) misses faces under ~90 px: on a real laptop-webcam frame it
    scored 0.0 when the frame was shrunk to 238 px wide (face ~55 px) and 0.92+ from 398 px wide. A
    landscape frame from the browser (960x540) with the face at ~22% of its width must keep it >= 120 px."""
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    small = pad.detector_input(frame)
    face_px = 0.22 * small.shape[1]
    assert face_px >= 120, small.shape


def test_small_frames_are_not_upscaled():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    assert pad.detector_input(frame).shape == frame.shape


def test_custom_model_only_vetoes_frames_it_scores_below_its_threshold():
    assert pad.veto(0.97, None) == 0.97
    assert pad.veto(0.97, 0.5) == 0.97 and pad.veto(0.97, 0.99) == 0.97
    assert abs(pad.veto(0.97, 0.25) - 0.485) < 1e-9
    assert pad.veto(0.97, 0.0) == 0.0


def test_custom_model_loads_and_scores_in_range(monkeypatch):
    models = Path(__file__).resolve().parents[3] / "models"   # the committed files, not conftest's empty dir
    monkeypatch.setattr(artifacts, "resolve", lambda rel: models / rel)
    p = pad.FacePAD()
    assert p._custom is not None and p.version.endswith("+mobilenetv3-custom")
    sess, name = p._custom
    out = float(sess.run(None, {name: np.zeros((1, 3, 128, 128), np.float32)})[0].reshape(-1)[0])
    assert 0.0 <= out <= 1.0
