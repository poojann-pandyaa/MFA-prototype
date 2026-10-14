"""Face /factor/verify with a scripted detector and PAD, so the decision logic is tested
without the model weights."""
import numpy as np
import pytest

from amfa.common.contract import FAILED, PASSED, SPOOF_DETECTED, PadMode, VerifyRequest
from amfa.factors.face import matcher as fm
from amfa.factors.face import service

TENANT, USER = "tn_test", "cu_test"


class Script:
    """One entry per frame: (yaw, live_prob, identity). Identity picks the embedding."""

    def __init__(self, frames):
        self.frames = frames
        self.version = "fake"

    # frame images are 1x1 arrays holding the frame index
    def detect(self, img):
        yaw, _, _ = self.frames[int(img[0, 0, 0])]
        return [fm.DetectedFace(box=np.array([0, 0, 200, 200]), score=0.99, width=200, yaw=yaw)]

    def embed(self, img, face):
        _, _, who = self.frames[int(img[0, 0, 0])]
        v = np.zeros(8, dtype=np.float32)
        v[who] = 1.0
        return v

    def score(self, img):
        class P:
            live_prob = self.frames[int(img[0, 0, 0])][1]
        return P()


@pytest.fixture
def run(monkeypatch):
    tpl = np.zeros(8, dtype=np.float32)
    tpl[0] = 1.0
    monkeypatch.setattr(service.store, "get", lambda t, u: {"mean": tpl.tolist()})
    monkeypatch.setattr(service.guard, "check", lambda *a, **k: None)
    monkeypatch.setattr(service.guard, "remember", lambda *a, **k: None)

    def go(frames, action="TURN_LEFT", mode=PadMode.TERMINAL):
        script = Script(frames)
        monkeypatch.setattr(fm, "get", lambda: script)
        monkeypatch.setattr(service.fpad, "get", lambda: script)
        monkeypatch.setattr(service, "decode_image", lambda f: (np.full((1, 1, 3), int(f)), f"d{f}"))
        req = VerifyRequest(tenantId=TENANT, userId=USER, sessionId="s", transactionId="t",
                            challenge={"action": action}, padMode=mode,
                            payload={"frames": [str(i) for i in range(len(frames))]})
        return service.verify(req)
    return go


def turn(n_front=4, n_ramp=4, n_turned=4, live=0.95, who=0):
    yaws = [0.0] * n_front + list(np.linspace(0.0, 0.5, n_ramp + 2)[1:-1]) + [0.5] * n_turned
    return [(y, live, who) for y in yaws]


def test_real_turn_by_the_enrolled_user_passes(run):
    res = run(turn())
    assert res.status == PASSED, res.failureReason


def test_two_photos_do_not_pass_the_turn(run):
    res = run(turn(n_front=6, n_ramp=0, n_turned=6))
    assert res.status == FAILED
    assert res.failureReason == "HEAD_TURN_NOT_CONTINUOUS"


def test_turn_in_the_wrong_direction_fails(run):
    assert run(turn(), action="TURN_RIGHT").status == FAILED


def test_a_different_person_fails(run):
    res = run(turn(who=1))
    assert res.status == FAILED
    assert res.failureReason in ("FACE_MISMATCH", "IDENTITY_CHANGED_DURING_TURN")


def test_a_spliced_in_other_face_is_caught_by_every_live_frame_check(run):
    frames = turn()
    frames[6] = (frames[6][0], 0.95, 1)
    res = run(frames)
    assert res.status == FAILED
    assert res.failureReason == "IDENTITY_CHANGED_DURING_TURN"


def test_frames_pad_rejects_cannot_forge_the_turn(run):
    """Frontal frames are live; the turned frames are a low-liveness photo."""
    frames = [(0.0, 0.95, 0)] * 6 + [(0.5, 0.1, 0)] * 6
    res = run(frames, mode=PadMode.ADVISORY)
    assert res.status == FAILED
    assert res.failureReason in ("HEAD_TURN_NOT_OBSERVED", "NOT_ENOUGH_LIVE_FRAMES")


def test_a_spoof_verdict_is_terminal_when_the_tenant_says_so(run):
    res = run(turn(live=0.3), mode=PadMode.TERMINAL)
    assert res.status == SPOOF_DETECTED


def test_too_few_frames_are_rejected(run):
    assert run(turn(n_front=2, n_ramp=1, n_turned=2)).status == FAILED
