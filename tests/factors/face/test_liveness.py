"""The burst-level liveness rule (service.liveness) on face setup and on the face check.

The PHONE_* and LAPTOP cases are the MiniFASNet scores and head angles measured on 2026-11-16 from a real
phone in a dim room and a laptop webcam; every one of them failed under the old rule."""
import os
import subprocess
import sys

import numpy as np
import pytest

from amfa.common.contract import FAILED, PASSED, SPOOF_DETECTED, EnrollRequest, PadMode, VerifyRequest
from amfa.factors.face import matcher as fm
from amfa.factors.face import service

TENANT, USER = "tn_test", "cu_test"

# (yaw, live) per frame; setup asks for straight, left, right, straight on the phone and straight x2,
# left, right, straight on the web
PHONE_1 = [(-0.009, 0.915), (0.201, 0.726), (-0.197, 0.857), (0.006, 0.985)]
PHONE_2 = [(0.000, 0.945), (0.194, 0.317), (-0.218, 0.681), (-0.024, 0.955)]
LAPTOP = [(0.006, 1.0), (0.009, 1.0), (0.244, 1.0), (-0.167, 1.0), (0.016, 1.0)]
# the same PHONE_2 frames as scored with the custom model vetoing them
PHONE_2_VETOED = [(0.000, 0.276), (0.194, 0.189), (-0.218, 0.504), (-0.024, 0.638)]


class Script:
    """One entry per frame: (yaw, live_prob, identity). Frame images are 1x1 arrays holding the index."""

    def __init__(self, frames):
        self.frames = frames
        self.version = "fake"

    def detect(self, img):
        yaw = self.frames[int(img[0, 0, 0])][0]
        return [fm.DetectedFace(box=np.array([0, 0, 200, 200]), score=0.99, width=200, yaw=yaw)]

    def embed(self, img, face):
        v = np.zeros(8, dtype=np.float32)
        v[self.frames[int(img[0, 0, 0])][2]] = 1.0
        return v

    def score(self, img):
        class P:
            live_prob = self.frames[int(img[0, 0, 0])][1]
            custom_prob = 0.05
        return P()


@pytest.fixture
def setup(monkeypatch):
    saved = {}
    monkeypatch.setattr(service.store, "put", lambda t, u, tpl, meta=None: saved.update(tpl=tpl, meta=meta))
    monkeypatch.setattr(service.guard, "remember", lambda *a, **k: None)

    def go(frames):
        frames = [f if len(f) == 3 else (*f, 0) for f in frames]
        script = Script(frames)
        monkeypatch.setattr(fm, "get", lambda: script)
        monkeypatch.setattr(service.fpad, "get", lambda: script)
        monkeypatch.setattr(service, "decode_image", lambda f: (np.full((1, 1, 3), int(f)), f"d{f}"))
        res = service.enroll(EnrollRequest(tenantId=TENANT, userId=USER,
                                           payload={"frames": [str(i) for i in range(len(frames))]}))
        return res, saved
    return go


@pytest.fixture
def check(monkeypatch):
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
        return service.verify(VerifyRequest(tenantId=TENANT, userId=USER, sessionId="s", transactionId="t",
                                            challenge={"action": action}, padMode=mode,
                                            payload={"frames": [str(i) for i in range(len(frames))]}))
    return go


def burst(front=(0.95,) * 4, ramp=(0.95,) * 4, turned=(0.95,) * 12, who=0):
    """A face-check burst like the app's (20 frames): straight on, a turn, and a hold."""
    yaws = [0.0] * len(front) + list(np.linspace(0.0, 0.5, len(ramp) + 2)[1:-1]) + [0.5] * len(turned)
    return [(y, p, who) for y, p in zip(yaws, [*front, *ramp, *turned])]


# --- the rule itself ---------------------------------------------------------------------------------

@pytest.mark.parametrize("frames", [PHONE_1, PHONE_2, LAPTOP], ids=["phone-1", "phone-2", "laptop"])
def test_the_measured_real_customers_are_live(frames):
    ok, info = service.liveness([p for _, p in frames], [y for y, _ in frames])
    assert ok, info


def test_with_the_custom_model_vetoing_the_same_phone_frames_still_fail():
    """Why the custom model is in shadow: no threshold change rescues these."""
    ok, _ = service.liveness([p for _, p in PHONE_2_VETOED], [y for y, _ in PHONE_2_VETOED])
    assert not ok


@pytest.mark.parametrize("lives, yaws, why", [
    ([0.05, 0.04, 0.06, 0.05], [0.0, 0.2, -0.2, 0.0], "a printed photo: nothing live"),
    ([0.60, 0.62, 0.61, 0.60], [0.0, 0.2, -0.2, 0.0], "a screen replay scored middling: below 0.70"),
    ([0.45, 0.99, 0.99, 0.48], [0.0, 0.2, -0.2, 0.0], "straight-on frames weak, only turned ones live"),
    ([0.95, 0.99, 0.30, 0.45], [0.0, 0.2, -0.2, 0.0], "straight-on frame below the frame floor"),
    ([0.95, 0.30, 0.20, 0.95], [0.0, 0.2, -0.2, 0.0], "half the burst not live: a turn from a photo"),
    ([0.95, 0.95, 0.95, 0.95], [0.3, 0.2, -0.2, 0.25], "no straight-on frame to judge"),
    ([], [], "no frames"),
])
def test_what_is_not_live(lives, yaws, why):
    ok, _ = service.liveness(lives, yaws)
    assert not ok, why


def test_the_threshold_is_inclusive_at_0_70():
    assert service.LIVENESS_THRESHOLD == 0.70
    assert service.liveness([0.70, 0.95, 0.95, 0.70], [0.0, 0.2, -0.2, 0.0])[0]
    assert not service.liveness([0.69, 0.95, 0.95, 0.69], [0.0, 0.2, -0.2, 0.0])[0]


def test_one_weak_frame_in_four_is_tolerated_but_not_two():
    assert service.liveness([0.95, 0.40, 0.95, 0.95], [0.0, 0.2, -0.2, 0.0])[0]
    assert not service.liveness([0.95, 0.40, 0.40, 0.95], [0.0, 0.2, -0.2, 0.0])[0]


def test_the_threshold_can_be_raised_but_never_set_below_0_50():
    code = "from amfa.factors.face import service; print(service.LIVENESS_THRESHOLD)"
    env = {**os.environ}

    def read(value):
        env["AMFA_FACE_LIVENESS_THRESHOLD"] = value
        return float(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                                    text=True, check=True).stdout.strip())
    assert read("0.85") == 0.85
    assert read("0.10") == 0.50


# --- face setup ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("frames", [PHONE_1, PHONE_2, LAPTOP], ids=["phone-1", "phone-2", "laptop"])
def test_the_measured_customers_can_set_up_their_face(setup, frames):
    res, saved = setup(frames)
    assert res.enrolled, (res.reason, res.quality)
    assert res.quality["frontalFrames"] >= 2 and saved["tpl"]["mean"]


def test_a_photo_cannot_set_up_a_face(setup):
    res, saved = setup([(0.0, 0.05), (0.2, 0.04), (-0.2, 0.06), (0.0, 0.05)])
    assert not res.enrolled and res.reason == "LIVENESS_NOT_ESTABLISHED" and not saved
    assert res.quality["frontalLiveness"] == 0.05


def test_a_turn_faked_with_photos_cannot_set_up_a_face(setup):
    res, _ = setup([(0.0, 0.95), (0.2, 0.2), (-0.2, 0.1), (0.0, 0.95)])
    assert not res.enrolled and res.reason == "LIVENESS_NOT_ESTABLISHED"
    assert res.quality["liveFraction"] == 0.5


def test_setup_still_needs_the_same_person_in_every_frame(setup):
    res, _ = setup([(0.0, 0.95, 0), (0.2, 0.95, 0), (-0.2, 0.95, 1), (0.0, 0.95, 0)])
    assert not res.enrolled and res.reason == "INCONSISTENT_IDENTITY"


def test_setup_still_refuses_repeated_frames(setup, monkeypatch):
    monkeypatch.setattr(service, "decode_image", lambda f: (np.full((1, 1, 3), 0), "same"))
    res = service.enroll(EnrollRequest(tenantId=TENANT, userId=USER, payload={"frames": ["0"] * 4}))
    assert not res.enrolled and res.reason == "DUPLICATE_FRAMES"


# --- the face check during a payment ------------------------------------------------------------------

def test_a_phone_burst_with_some_weak_turned_frames_passes(check):
    """A quarter of the frames weak (a turned head in dim light) and the rest live."""
    res = check(burst(turned=(0.35,) * 5 + (0.95,) * 7))
    assert res.status == PASSED, (res.failureReason, res.detail)
    assert res.livenessPassed and res.detail["active"]["liveFraction"] == 0.75


def test_the_lower_threshold_lets_a_real_face_at_0_72_through(check):
    res = check(burst(front=(0.72,) * 4, ramp=(0.72,) * 4))
    assert res.status == PASSED, res.failureReason
    assert res.livenessScore == 0.72


def test_a_face_below_the_threshold_is_a_spoof(check):
    res = check(burst(front=(0.65,) * 4, ramp=(0.65,) * 4, turned=(0.65,) * 12))
    assert res.status == SPOOF_DETECTED and res.failureReason == "PRESENTATION_ATTACK_DETECTED"


def test_too_many_weak_frames_are_a_spoof(check):
    res = check(burst(turned=(0.2,) * 8 + (0.95,) * 4))
    assert res.status == SPOOF_DETECTED


def test_photos_spliced_into_the_straight_on_frames_are_a_spoof(check):
    res = check(burst(front=(0.95, 0.2, 0.2, 0.95)))          # 3 of the 5 straight-on frames live: below 75%
    assert res.status == SPOOF_DETECTED


# the face check measured on the phone on 2026-11-18: 17 straight-on frames with one blink or blur at 0.201, then a turn
PHONE_CHECK = [0.977, 0.958, 0.201, 0.990, 0.998, 0.986, 0.983, 0.992, 0.995, 0.986, 0.987, 0.975, 0.985, 0.978,
               0.973, 0.975, 0.945, 0.870, 0.514, 0.544, 0.647, 0.582]


def test_one_blink_among_many_straight_on_frames_does_not_make_a_real_face_a_spoof(check):
    frames = [(0.0, p, 0) for p in PHONE_CHECK[:16]] + [(0.05, PHONE_CHECK[16], 0)] + \
             [(y, p, 0) for y, p in zip((0.2, 0.3, 0.4, 0.5, 0.5), PHONE_CHECK[17:])]
    res = check(frames)
    assert res.status == PASSED and res.failureReason is None, (res.failureReason, res.detail)
    assert res.livenessPassed and res.detail["active"]["frontalLiveFraction"] < 1.0


def test_in_advisory_mode_a_weak_burst_is_reported_not_passed_silently(check):
    res = check(burst(front=(0.65,) * 4, ramp=(0.65,) * 4, turned=(0.65,) * 12), mode=PadMode.ADVISORY)
    assert res.status == PASSED and res.failureReason == "PAD_ADVISORY_SPOOF_SUSPECTED"
    assert not res.livenessPassed


def test_the_custom_models_scores_are_kept_for_recalibration(check):
    res = check(burst())
    assert res.detail["active"]["frameCustom"] == [0.05] * 20


def test_a_different_person_still_fails(check):
    res = check(burst(who=1))
    assert res.status == FAILED and res.failureReason in ("FACE_MISMATCH", "IDENTITY_CHANGED_DURING_TURN")
