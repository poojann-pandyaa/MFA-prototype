"""
Pure landmark-sequence-in, bool-out gesture verification - no I/O, no
model loading, so it's directly unit-testable with synthetic landmark
arrays (see tests/test_liveness_challenge.py). Landmark indices below
are the standard MediaPipe Face Mesh topology indices (478-point model);
if verify_gesture looks miscalibrated against real captures, re-check
these against your installed mediapipe version's canonical face model
before assuming the thresholds are wrong.

Sign convention: turn_left/turn_right compare the FIRST and LAST frame's
nose position (not min/max), so a brief twitch doesn't count - only a
sustained displacement across the whole burst does. Whether a rightward
pixel shift means the subject's "left" or "right" depends on whether the
frames you're capturing are mirrored before this code sees them; confirm
the mapping against your own camera in Task 11's manual check and flip
the sign here if it's backwards for your setup.
"""
import numpy as np
import config

_LEFT_EYE_HORIZONTAL = (33, 133)
_LEFT_EYE_VERTICAL = [(159, 145), (158, 153)]
_RIGHT_EYE_HORIZONTAL = (362, 263)
_RIGHT_EYE_VERTICAL = [(386, 374), (387, 373)]
_NOSE_TIP = 1
_INTER_EYE = (133, 362)

_MIN_FRAMES = 3


def _ear(landmarks: np.ndarray, horizontal, vertical_pairs) -> float:
    h0, h1 = horizontal
    horiz = np.linalg.norm(landmarks[h0] - landmarks[h1])
    if horiz == 0:
        return 0.0
    vert = sum(np.linalg.norm(landmarks[a] - landmarks[b]) for a, b in vertical_pairs)
    vert /= len(vertical_pairs)
    return float(vert / horiz)


def _mean_ear(landmarks: np.ndarray) -> float:
    left = _ear(landmarks, _LEFT_EYE_HORIZONTAL, _LEFT_EYE_VERTICAL)
    right = _ear(landmarks, _RIGHT_EYE_HORIZONTAL, _RIGHT_EYE_VERTICAL)
    return (left + right) / 2


def _verify_blink(sequence) -> bool:
    ears = [_mean_ear(lm) for lm in sequence]
    baseline = max(ears)
    dip = min(ears)
    return bool(dip < config.BLINK_EAR_THRESHOLD and baseline >= config.BLINK_EAR_THRESHOLD)


def _verify_turn(sequence, direction: str) -> bool:
    normalized_x = []
    for lm in sequence:
        inter_eye = np.linalg.norm(lm[_INTER_EYE[0]] - lm[_INTER_EYE[1]])
        if inter_eye == 0:
            return False
        # float() here (not just relying on Python's float division) matters:
        # lm[_NOSE_TIP][0] and inter_eye are numpy scalars, so leaving them
        # as-is propagates numpy.float32/float64 through displacement below,
        # which then makes the threshold comparison return numpy.bool_
        # instead of a native bool - breaking `is True`/`is False` checks
        # (and anything else that expects this "bool-out" function to
        # actually return bool).
        normalized_x.append(float(lm[_NOSE_TIP][0]) / float(inter_eye))

    displacement = normalized_x[-1] - normalized_x[0]
    if direction == "turn_left":
        return bool(displacement > config.HEAD_TURN_DISPLACEMENT_THRESHOLD)
    return bool(displacement < -config.HEAD_TURN_DISPLACEMENT_THRESHOLD)


def verify_gesture(landmark_sequence, challenge_type: str) -> bool:
    if len(landmark_sequence) < _MIN_FRAMES:
        return False
    if challenge_type == "blink":
        return _verify_blink(landmark_sequence)
    if challenge_type in ("turn_left", "turn_right"):
        return _verify_turn(landmark_sequence, challenge_type)
    return False
