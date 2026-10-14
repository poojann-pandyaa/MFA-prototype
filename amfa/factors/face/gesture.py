from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# Head turn is measured relative to the face: the nose tip's offset from the eye midpoint,
# projected on the eye axis and divided by the inter-eye distance. Translation, leaning in or out
# and head tilt leave it unchanged; only a real yaw moves it. Landmarks are YuNet's five points
# (eye, eye, nose tip, mouth, mouth) on unmirrored frames, so turning to one's own left moves the
# nose toward image-right (positive offset change).
TURN_WINDOW = 3
IN_BETWEEN_LOW, IN_BETWEEN_HIGH = 0.15, 0.85


@dataclass(frozen=True)
class TurnStats:
    start: float
    end: float
    displacement: float
    in_between: int


def nose_offset(lm: np.ndarray) -> Optional[float]:
    eye_a, eye_b, nose = (np.asarray(lm[i], dtype=np.float64) for i in (0, 1, 2))
    axis = eye_b - eye_a
    iod = float(np.linalg.norm(axis))
    if not math.isfinite(iod) or iod <= 1e-3:
        return None
    return float(np.dot(nose - (eye_a + eye_b) / 2, axis) / (iod * iod))


def turn_stats(offsets: Sequence[float]) -> Optional[TurnStats]:
    """Compare the median of the first frames with the median of the last ones, so a single
    twitch or glitched landmark at either end cannot count, and count the frames that sit
    partway between the two poses. A real turn passes through them; a photo followed by its
    mirror image, or two photos in different poses, jumps straight across and has none."""
    if len(offsets) < 2:
        return None
    window = max(1, min(TURN_WINDOW, len(offsets) // 2))
    start = float(np.median(offsets[:window]))
    end = float(np.median(offsets[-window:]))
    displacement = end - start
    if displacement == 0:
        return TurnStats(start, end, 0.0, 0)
    in_between = sum(1 for o in offsets
                     if IN_BETWEEN_LOW < (o - start) / displacement < IN_BETWEEN_HIGH)
    return TurnStats(start, end, displacement, in_between)


def check_turn(offsets: Sequence[float], action: str, *, frontal_max: float, min_delta: float,
               min_in_between: int) -> tuple[Optional[str], Optional[TurnStats]]:
    """Returns (failure reason or None, measured stats)."""
    stats = turn_stats(offsets)
    if stats is None:
        return "HEAD_TURN_NOT_OBSERVED", None
    if abs(stats.start) > frontal_max:
        return "NO_FRONTAL_FRAME", stats
    sign = 1.0 if action == "TURN_LEFT" else -1.0
    if sign * stats.displacement < min_delta:
        return "HEAD_TURN_NOT_OBSERVED", stats
    if stats.in_between < min_in_between:
        return "HEAD_TURN_NOT_CONTINUOUS", stats
    return None, stats
