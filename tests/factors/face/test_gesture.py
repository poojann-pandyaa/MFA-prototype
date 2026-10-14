import numpy as np
import pytest

from amfa.factors.face import gesture

KW = dict(frontal_max=0.12, min_delta=0.18, min_in_between=2)


def landmarks(nose_dx=0.0, *, tx=0.0, ty=0.0, scale=1.0, roll=0.0):
    """Five YuNet points: right eye, left eye, nose, mouth x2. nose_dx is the nose's sideways
    offset from the eye midpoint in inter-eye units (a real yaw changes it)."""
    pts = np.array([[-0.5, 0.0], [0.5, 0.0], [nose_dx, 0.6], [-0.3, 1.2], [0.3, 1.2]])
    c, s = np.cos(roll), np.sin(roll)
    pts = pts @ np.array([[c, -s], [s, c]]).T
    return pts * 100 * scale + np.array([300 + tx, 300 + ty])


def offsets(seq):
    return [gesture.nose_offset(lm) for lm in seq]


def ramp(start, end, n):
    return list(np.linspace(start, end, n))


def check(offs, action="TURN_LEFT"):
    return gesture.check_turn(offs, action, **KW)[0]


def test_genuine_turn_left_passes_only_left():
    offs = [0.0] * 3 + ramp(0.0, 0.4, 6) + [0.4] * 3
    assert check(offs, "TURN_LEFT") is None
    assert check(offs, "TURN_RIGHT") == "HEAD_TURN_NOT_OBSERVED"


def test_genuine_turn_right_passes_only_right():
    offs = [0.0] * 3 + ramp(0.0, -0.4, 6) + [-0.4] * 3
    assert check(offs, "TURN_RIGHT") is None
    assert check(offs, "TURN_LEFT") == "HEAD_TURN_NOT_OBSERVED"


@pytest.mark.parametrize("kw", [dict(tx=80), dict(ty=60), dict(scale=1.5)])
def test_translation_and_scale_are_not_a_turn(kw):
    seq = [landmarks(0.0)] * 4 + [landmarks(0.0, **kw)] * 6
    for action in ("TURN_LEFT", "TURN_RIGHT"):
        assert check(offsets(seq), action) == "HEAD_TURN_NOT_OBSERVED"


def test_head_tilt_is_not_a_turn():
    seq = [landmarks(0.0, roll=r) for r in np.linspace(0, 0.5, 10)]
    for action in ("TURN_LEFT", "TURN_RIGHT"):
        assert check(offsets(seq), action) == "HEAD_TURN_NOT_OBSERVED"


def test_two_photos_jumping_between_poses_fail():
    """The attack the face-auth branch closed in 46244ad: a frontal photo, then a turned photo."""
    offs = [0.0] * 5 + [0.4] * 5
    assert check(offs) == "HEAD_TURN_NOT_CONTINUOUS"


def test_one_in_between_frame_is_not_enough():
    offs = [0.0] * 4 + [0.2] + [0.4] * 4
    assert check(offs) == "HEAD_TURN_NOT_CONTINUOUS"


def test_fast_but_real_turn_passes():
    offs = [0.0] * 3 + [0.13, 0.27] + [0.4] * 4
    assert check(offs) is None


def test_one_outlier_end_frame_cannot_fake_a_turn():
    offs = [0.0] * 9 + [0.5]
    assert check(offs) == "HEAD_TURN_NOT_OBSERVED"


def test_turn_must_start_from_a_frontal_pose():
    offs = [0.3] * 3 + ramp(0.3, 0.7, 6) + [0.7] * 3
    assert check(offs) == "NO_FRONTAL_FRAME"


def test_too_few_frames_fail_closed():
    assert check([0.0]) == "HEAD_TURN_NOT_OBSERVED"


def test_degenerate_eyes_have_no_offset():
    lm = landmarks(0.0)
    lm[1] = lm[0]
    assert gesture.nose_offset(lm) is None
