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


def test_custom_model_mode_defaults_to_shadow_and_reads_old_values():
    assert pad.custom_mode(None) == pad.SHADOW and pad.custom_mode("") == pad.SHADOW
    assert pad.custom_mode("shadow") == pad.SHADOW and pad.custom_mode(" VETO ") == pad.VETO
    assert pad.custom_mode("off") == pad.OFF
    assert pad.custom_mode("0") == pad.OFF and pad.custom_mode("1") == pad.VETO   # the old on/off switch
    assert pad.custom_mode("nonsense") == pad.SHADOW


class _Sess:
    """A stand-in ONNX session that always returns the same output."""

    def __init__(self, out):
        self.out = np.asarray(out, dtype=np.float32)

    def run(self, _names, _feeds):
        return [self.out]


def _pad(mode, minifas_live, custom_live):
    p = pad.FacePAD.__new__(pad.FacePAD)
    p.custom_mode = mode
    p._bbox = lambda img: [10, 10, 60, 60]
    logits = np.log([[0.5 * (1 - minifas_live), minifas_live, 0.5 * (1 - minifas_live)]])
    p._models = [(_Sess(logits), "x", 80, 80, 2.7), (_Sess(logits), "x", 80, 80, 4.0)]
    p._custom = None if mode == pad.OFF else (_Sess([[custom_live]]), "x")
    return p


IMG = np.zeros((120, 120, 3), dtype=np.uint8)


def test_shadow_mode_records_the_custom_score_but_does_not_change_the_live_score():
    """The measured case: MiniFASNet sure the face is real, the custom model not."""
    f = _pad(pad.SHADOW, 0.95, 0.05).score(IMG)
    assert abs(f.live_prob - 0.95) < 1e-4 and abs(f.custom_prob - 0.05) < 1e-6


def test_veto_mode_still_scales_the_live_score_down():
    f = _pad(pad.VETO, 0.95, 0.05).score(IMG)
    assert abs(f.live_prob - 0.95 * 0.05 / 0.5) < 1e-4


def test_off_mode_has_no_custom_score():
    f = _pad(pad.OFF, 0.95, 0.05).score(IMG)
    assert f.custom_prob is None and abs(f.live_prob - 0.95) < 1e-4


def test_custom_model_loads_and_scores_in_range(monkeypatch):
    models = Path(__file__).resolve().parents[3] / "models"   # the committed files, not conftest's empty dir
    monkeypatch.setattr(artifacts, "resolve", lambda rel: models / rel)
    p = pad.FacePAD(custom=pad.VETO)
    assert p._custom is not None and p.version.endswith("+mobilenetv3-custom")
    assert pad.FacePAD(custom=pad.SHADOW).version.endswith("+mobilenetv3-custom-shadow")
    assert pad.FacePAD(custom=pad.OFF)._custom is None
    sess, name = p._custom
    out = float(sess.run(None, {name: np.zeros((1, 3, 128, 128), np.float32)})[0].reshape(-1)[0])
    assert 0.0 <= out <= 1.0
