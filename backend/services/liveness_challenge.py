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
change across the burst does. The change must also build up: at least
HEAD_TURN_MIN_INTERMEDIATE_FRAMES frames must sit partway between the start
and end pose, so a photo followed by its mirror image (or two photos in
different poses), which jumps straight from one pose to the other, fails.

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


def _blink_stats(sequence):
    """(min EAR, max EAR, open->closed->open found). A blink is some closed
    frame (EAR below the threshold) with an open frame (EAR at/above it)
    somewhere BEFORE it and somewhere AFTER it. Eyes that close and stay
    closed, or that start closed and open, are not a blink."""
    threshold = config.BLINK_EAR_THRESHOLD
    ears = [_mean_ear(lm) for lm in sequence]
    is_open = [e >= threshold for e in ears]
    found = any(
        ear < threshold and any(is_open[:i]) and any(is_open[i + 1:])
        for i, ear in enumerate(ears)
    )
    return min(ears), max(ears), found


def _verify_blink(sequence) -> bool:
    return _blink_stats(sequence)[2]


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


def _turn_stats(sequence):
    """(displacement, in_between_frames), or None if any frame's eye
    landmarks are degenerate.

    displacement: median(last window) - median(first window) of the
    face-relative nose offset; positive = toward image-right (turn_left).

    in_between_frames: how many frames sit partway (15%-85%) from the start
    pose to the end pose. A real turn passes through those poses; a photo
    followed by its mirror image, or two photos in different poses, jumps
    straight from one to the other and has none."""
    offsets = []
    for lm in sequence:
        offset = _face_relative_nose_offset(lm)
        if offset is None:
            return None
        offsets.append(offset)

    window = max(1, min(_TURN_WINDOW, len(offsets) // 2))
    start = float(np.median(offsets[:window]))
    end = float(np.median(offsets[-window:]))
    displacement = end - start
    if displacement == 0:
        return displacement, 0
    in_between = sum(1 for o in offsets if 0.15 < (o - start) / displacement < 0.85)
    return displacement, in_between


def _turn_displacement(sequence):
    stats = _turn_stats(sequence)
    return None if stats is None else stats[0]


def _verify_turn(sequence, direction: str) -> bool:
    stats = _turn_stats(sequence)
    if stats is None:
        return False
    displacement, in_between = stats
    if in_between < config.HEAD_TURN_MIN_INTERMEDIATE_FRAMES:
        return False
    if direction == "turn_left":
        return bool(displacement > config.HEAD_TURN_DISPLACEMENT_THRESHOLD)
    return bool(displacement < -config.HEAD_TURN_DISPLACEMENT_THRESHOLD)


def describe_gesture(landmark_sequence, challenge_type: str) -> str:
    """Server-side diagnostics only (never sent to clients): the measured
    values verify_gesture decides on, computed with the same helpers."""
    n = len(landmark_sequence)
    if n < _MIN_FRAMES:
        return f"frames={n} (< min {_MIN_FRAMES})"
    if challenge_type == "blink":
        min_ear, max_ear, found = _blink_stats(landmark_sequence)
        return (f"min_ear={min_ear:.3f} max_ear={max_ear:.3f} "
                f"ear_threshold={config.BLINK_EAR_THRESHOLD:.3f} open_closed_open={found}")
    if challenge_type in ("turn_left", "turn_right"):
        stats = _turn_stats(landmark_sequence)
        threshold = config.HEAD_TURN_DISPLACEMENT_THRESHOLD
        if stats is None:
            return "turn_delta=n/a (degenerate eye landmarks)"
        displacement, in_between = stats
        return (f"turn_delta={displacement:+.3f} threshold={threshold:.3f} "
                f"(turn_left needs > +{threshold:.3f}, turn_right needs < -{threshold:.3f}) "
                f"in_between_frames={in_between} min={config.HEAD_TURN_MIN_INTERMEDIATE_FRAMES}")
    return "unknown challenge_type"


def verify_gesture(landmark_sequence, challenge_type: str) -> bool:
    if len(landmark_sequence) < _MIN_FRAMES:
        return False
    if challenge_type == "blink":
        return _verify_blink(landmark_sequence)
    if challenge_type in ("turn_left", "turn_right"):
        return _verify_turn(landmark_sequence, challenge_type)
    return False
