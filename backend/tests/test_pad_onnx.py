import numpy as np
from services import pad_onnx


def test_check_liveness_sequence_empty_frames_is_false():
    assert pad_onnx.check_liveness_sequence([], [], threshold=0.5, min_live_fraction=0.8) is False


def test_check_liveness_sequence_runs_on_blank_frames():
    # Blank frames won't score "live" against a real model, but this must
    # not crash - it's exercising the aggregation logic, not model accuracy.
    frames = [np.zeros((200, 200, 3), dtype=np.uint8) for _ in range(5)]
    bboxes = [(20, 20, 100, 100) for _ in range(5)]
    result = pad_onnx.check_liveness_sequence(frames, bboxes, threshold=0.5, min_live_fraction=0.8)
    assert isinstance(result, bool)
