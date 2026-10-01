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
