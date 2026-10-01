import numpy as np
import config
from services import liveness_challenge as lc


def _base_landmarks():
    # 478 points, all at origin except the ones verify_gesture actually
    # reads - only those indices matter for these tests.
    lm = np.zeros((478, 2), dtype=np.float32)
    # Eyes open, level, symmetric: horizontal corners wide apart,
    # vertical lid pairs far apart relative to horizontal -> high EAR.
    lm[33] = [100, 100]
    lm[133] = [140, 100]
    lm[159] = [115, 92]
    lm[145] = [115, 108]
    lm[158] = [125, 92]
    lm[153] = [125, 108]
    lm[362] = [200, 100]
    lm[263] = [240, 100]
    lm[386] = [215, 92]
    lm[374] = [215, 108]
    lm[387] = [225, 92]
    lm[373] = [225, 108]
    lm[1] = [170, 120]  # nose tip, centered between the eyes
    return lm


def _closed_eyes(lm):
    lm = lm.copy()
    # Vertical lid pairs collapse together -> low EAR (eyes closed).
    lm[159] = [115, 99]
    lm[145] = [115, 101]
    lm[158] = [125, 99]
    lm[153] = [125, 101]
    lm[386] = [215, 99]
    lm[374] = [215, 101]
    lm[387] = [225, 99]
    lm[373] = [225, 101]
    return lm


def test_blink_sequence_with_dip_and_recovery_passes():
    open_lm = _base_landmarks()
    closed_lm = _closed_eyes(open_lm)
    sequence = [open_lm, open_lm, closed_lm, open_lm, open_lm]
    assert lc.verify_gesture(sequence, "blink") is True


def test_blink_sequence_with_no_dip_fails():
    open_lm = _base_landmarks()
    sequence = [open_lm] * 5
    assert lc.verify_gesture(sequence, "blink") is False


def test_turn_left_sequence_passes():
    lm = _base_landmarks()
    sequence = []
    for i in range(5):
        shifted = lm.copy()
        shifted[1] = [170 + i * 15, 120]  # nose moves steadily toward image-right
        sequence.append(shifted)
    assert lc.verify_gesture(sequence, "turn_left") is True
    assert lc.verify_gesture(sequence, "turn_right") is False


def test_too_short_sequence_fails_closed():
    assert lc.verify_gesture([_base_landmarks()], "blink") is False
    assert lc.verify_gesture([], "blink") is False
