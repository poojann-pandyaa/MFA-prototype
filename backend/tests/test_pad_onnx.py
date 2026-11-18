from unittest import mock

import numpy as np
from services import pad_onnx


def test_check_liveness_sequence_empty_frames_is_false():
    assert pad_onnx.check_liveness_sequence([], [], threshold=0.5, min_live_fraction=0.8) is False


def test_check_liveness_sequence_respects_min_live_fraction():
    frames = [np.zeros((200, 200, 3), dtype=np.uint8) for _ in range(5)]
    bboxes = [(20, 20, 100, 100) for _ in range(5)]

    # 3 of 5 frames score "live" (label=1, confidence=0.9) -> live_fraction = 0.6
    three_live = [(1, 0.9), (1, 0.9), (1, 0.9), (0, 0.9), (0, 0.9)]

    with mock.patch.object(pad_onnx._predictor, "predict_with_bbox", side_effect=three_live):
        assert pad_onnx.check_liveness_sequence(frames, bboxes, threshold=0.5, min_live_fraction=0.6) is True

    with mock.patch.object(pad_onnx._predictor, "predict_with_bbox", side_effect=three_live):
        assert pad_onnx.check_liveness_sequence(frames, bboxes, threshold=0.5, min_live_fraction=0.7) is False


# ---- MFA_PAD_CUSTOM_MODE: who decides, MiniFASNet or the custom model ---------------------------------------

class _Mini:
    """A stand-in MiniFASNet model: fixed softmax output, class 1 is live."""
    scale, w_input, h_input = 2.7, 80, 80

    def __init__(self, live):
        self.out = np.array([[(1 - live) / 2, live, (1 - live) / 2]], dtype=np.float32)

    def predict(self, crop):
        return self.out


class _Custom:
    def __init__(self, live):
        self.live = live

    def predict_probability(self, crop):
        return self.live


class _Cropper:
    def crop(self, **kw):
        return np.zeros((kw["out_h"], kw["out_w"], 3), dtype=np.uint8)


def _predictor(mode, minifas_live, custom_live):
    p = pad_onnx.AntiSpoofPredictor.__new__(pad_onnx.AntiSpoofPredictor)
    p.cropper, p.custom_mode, p.last_custom_prob = _Cropper(), mode, None
    p.models = [] if mode == "replace" else [_Mini(minifas_live), _Mini(minifas_live)]
    p.custom_model = _Custom(custom_live)
    return p


IMG, BOX = np.zeros((200, 200, 3), dtype=np.uint8), (20, 20, 100, 100)


def test_the_custom_model_is_in_shadow_by_default():
    assert pad_onnx.CUSTOM_MODE == "shadow"


def test_in_shadow_minifasnet_decides_and_the_custom_score_is_kept():
    """The measured case: MiniFASNet sure the face is real (0.95), the custom model not (0.05)."""
    p = _predictor("shadow", 0.95, 0.05)
    label, confidence = p.predict_with_bbox(IMG, BOX)
    assert label == 1 and abs(confidence - 0.95) < 1e-5 and p.last_custom_prob == 0.05


def test_in_shadow_minifasnet_still_rejects_what_it_judges_a_spoof():
    label, _ = _predictor("shadow", 0.10, 0.99).predict_with_bbox(IMG, BOX)
    assert label != 1


def test_replace_mode_lets_the_custom_model_decide_as_before():
    label, confidence = _predictor("replace", 0.95, 0.05).predict_with_bbox(IMG, BOX)
    assert label == 0 and confidence == 0.05


def test_the_loaded_predictor_runs_minifasnet_alongside_the_custom_model():
    p = pad_onnx.AntiSpoofPredictor()
    assert p.ready and len(p.models) == 2 and p.custom_model is not None


def test_live_frame_mask_records_the_custom_scores(monkeypatch):
    monkeypatch.setattr(pad_onnx, "_predictor", _predictor("shadow", 0.95, 0.05))
    mask = pad_onnx.live_frame_mask([IMG, IMG], [BOX, BOX], threshold=0.5)
    assert mask == [True, True] and pad_onnx.last_custom_scores == [0.05, 0.05]


def test_one_weak_frame_in_four_passes_the_default_burst_rule():
    from config import LIVENESS_MIN_LIVE_FRAME_FRACTION
    assert LIVENESS_MIN_LIVE_FRAME_FRACTION == 0.75
    assert pad_onnx.mask_passes([True, False, True, True], LIVENESS_MIN_LIVE_FRAME_FRACTION)
    assert not pad_onnx.mask_passes([True, False, False, True], LIVENESS_MIN_LIVE_FRAME_FRACTION)
