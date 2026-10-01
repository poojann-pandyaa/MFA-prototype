"""
Pure landmark-sequence-in, bool-out gesture verification - no I/O, no
model loading, so it's directly unit-testable with synthetic landmark
arrays (see tests/test_liveness_challenge.py). Landmark indices below
are the standard MediaPipe Face Mesh topology indices (478-point model);
if verify_gesture looks miscalibrated against real captures, re-check
these against your installed mediapipe version's canonical face model
before assuming the thresholds are wrong.

Head turn is measured RELATIVE TO THE FACE, never in absolute image
coordinates: per frame, the nose tip's offset from the midpoint of the two
inner eye corners, projected onto the eye axis and divided by the inner-eye
distance. That quantity is invariant to translation (sliding in frame),
uniform scale (leaning in/out) and in-plane rotation (head tilt), so only a
real yaw - the nose moving sideways relative to the eyes - changes it.

turn_left/turn_right compare the median of the FIRST few frames with the
median of the LAST few (not min/max, not single frames), so neither a brief
twitch nor one glitched landmark at either end counts - only a sustained
change across the burst does.

Sign convention: frames arrive unmirrored (react-webcam mirrored=false), so
a subject turning to their own left moves the nose toward image-right
(positive offset change) -> turn_left. If you ever mirror frames before
they reach this code, flip the sign here.
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
# How many frames at each end of the burst are median-pooled for the turn
# comparison (capped at half the sequence so the windows never overlap).
_TURN_WINDOW = 3


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
    """A blink is open -> closed -> open: some closed frame (EAR below the
    threshold) must have an open frame (EAR at/above it) somewhere BEFORE it
    and somewhere AFTER it. Eyes that close and stay closed, or that start
    closed and open, are not a blink."""
    threshold = config.BLINK_EAR_THRESHOLD
    ears = [_mean_ear(lm) for lm in sequence]
    is_open = [e >= threshold for e in ears]
    for i, ear in enumerate(ears):
        if ear < threshold and any(is_open[:i]) and any(is_open[i + 1:]):
            return True
    return False


def _face_relative_nose_offset(lm: np.ndarray):
    """Nose tip's offset from the inner-eye midpoint, measured along the
    eye axis (image-left eye -> image-right eye), in units of inner-eye
    distance. None if the eye landmarks are degenerate."""
    eye_a = lm[_INTER_EYE[0]].astype(np.float64)
    eye_b = lm[_INTER_EYE[1]].astype(np.float64)
    axis = eye_b - eye_a
    inter_eye = float(np.linalg.norm(axis))
    if not np.isfinite(inter_eye) or inter_eye == 0:
        return None
    midpoint = (eye_a + eye_b) / 2
    nose = lm[_NOSE_TIP].astype(np.float64)
    # float() keeps this a native Python float, so the threshold comparison
    # below yields a real bool rather than numpy.bool_ (verify_gesture's
    # "bool-out" contract, and `is True`/`is False` checks, rely on it).
    return float(np.dot(nose - midpoint, axis) / (inter_eye * inter_eye))


def _verify_turn(sequence, direction: str) -> bool:
    offsets = []
    for lm in sequence:
        offset = _face_relative_nose_offset(lm)
        if offset is None:
            return False
        offsets.append(offset)

    window = max(1, min(_TURN_WINDOW, len(offsets) // 2))
    start = float(np.median(offsets[:window]))
    end = float(np.median(offsets[-window:]))
    displacement = end - start
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
