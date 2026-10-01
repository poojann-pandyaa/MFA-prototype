import numpy as np
from services import face_detect


def test_detect_returns_none_bbox_for_blank_frame():
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    result = face_detect.detect(blank)
    assert result.bbox is None
    assert result.landmarks_px is None
    assert result.multi_face is False
