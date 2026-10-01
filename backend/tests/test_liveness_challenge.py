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


# --- Head-turn must be measured relative to the face, not the frame -------
#
# Each helper below builds a 13-frame burst (the frontend's burst size) by
# applying a transform progressively from t=0 (first frame) to t=1 (last).
# Translation, uniform scaling (leaning in/out) and in-plane rotation (head
# tilt/roll) move every landmark together and are NOT a head turn, so they
# must fail both turn directions no matter how large they are.

_BURST = 13


def _burst(transform, n=_BURST):
    base = _base_landmarks()
    return [transform(base.copy(), i / (n - 1)) for i in range(n)]


def _translate(dx, dy=0.0):
    def f(lm, t):
        lm[:, 0] += dx * t
        lm[:, 1] += dy * t
        return lm
    return f


def _scale(factor, center):
    c = np.array(center, dtype=np.float32)

    def f(lm, t):
        k = 1 + (factor - 1) * t
        return ((lm - c) * k + c).astype(np.float32)
    return f


def _roll(degrees, center=(170, 110)):
    c = np.array(center, dtype=np.float32)

    def f(lm, t):
        a = np.deg2rad(degrees * t)
        r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]], dtype=np.float32)
        return ((lm - c) @ r.T + c).astype(np.float32)
    return f


def _yaw(nose_shift_px, then=None):
    """Genuine head turn: the nose tip moves sideways RELATIVE to the eyes.
    Optionally composed with another (non-turn) transform, since a real
    turn also comes with some drift/lean."""
    def f(lm, t):
        lm[1, 0] += nose_shift_px * t
        return then(lm, t) if then else lm
    return f


def _both_directions(sequence):
    return (lc.verify_gesture(sequence, "turn_left"), lc.verify_gesture(sequence, "turn_right"))


def test_pure_translation_fails_both_turn_directions():
    for dx, dy in [(10, 0), (-10, 0), (60, 0), (-60, 0), (25, 15), (0, -40)]:
        assert _both_directions(_burst(_translate(dx, dy))) == (False, False), (dx, dy)


def test_pure_scaling_fails_both_turn_directions():
    centers = [(0, 0), (170, 110), (360, 270), (1000, -200), (-500, 800)]
    for factor in [1.03, 0.97, 1.5, 0.6]:
        for center in centers:
            assert _both_directions(_burst(_scale(factor, center))) == (False, False), (factor, center)


def test_head_tilt_roll_fails_both_turn_directions():
    # Realistic proportions: nose tip ~0.67x the inner-eye spacing below
    # the eye line. Tilting the head (in-plane roll about the eye midpoint)
    # swings the nose sideways in IMAGE x by 40*sin(angle) px - a naive
    # (nose_x - eye_mid_x) / inter_eye metric would read a 20deg tilt as a
    # 0.23 "turn". Measuring along the eye axis cancels the roll.
    def realistic(lm, t):
        lm[1] = [170, 140]
        return lm

    for degrees in [10, -10, 20, -20, 30, -30]:
        roll = _roll(degrees, center=(170, 100))
        sequence = _burst(lambda lm, t, roll=roll: roll(realistic(lm, t), t))
        assert _both_directions(sequence) == (False, False), degrees


def test_genuine_turn_left_passes_only_turn_left():
    # Subject turns to their own left -> nose moves toward image-right
    # relative to the eyes (unmirrored frames). 30px over a 60px inner-eye
    # spacing = 0.5 normalized offset, well over the 0.15 threshold.
    for then in [None, _translate(-20), _scale(1.1, (360, 270))]:
        assert _both_directions(_burst(_yaw(30, then))) == (True, False)


def test_genuine_turn_right_passes_only_turn_right():
    for then in [None, _translate(20), _scale(0.9, (360, 270))]:
        assert _both_directions(_burst(_yaw(-30, then))) == (False, True)


def test_turn_uses_window_median_so_one_outlier_end_frame_cannot_fake_it():
    # Static face, but the very last frame's nose landmark glitches far to
    # the right: comparing single first/last frames would read that as a
    # turn_left; medians over the first/last frames must not.
    sequence = _burst(_translate(0))
    sequence[-1][1, 0] += 60
    assert _both_directions(sequence) == (False, False)


def test_blink_eyes_close_and_stay_closed_fails():
    open_lm = _base_landmarks()
    closed_lm = _closed_eyes(open_lm)
    sequence = [open_lm, open_lm, closed_lm, closed_lm, closed_lm]
    assert lc.verify_gesture(sequence, "blink") is False


def test_blink_eyes_open_from_closed_without_prior_open_fails():
    open_lm = _base_landmarks()
    closed_lm = _closed_eyes(open_lm)
    sequence = [closed_lm, closed_lm, open_lm, open_lm, open_lm]
    assert lc.verify_gesture(sequence, "blink") is False
