"""Voice /factor/identify with a scripted speaker model and PAD, so no model weights are needed."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from amfa.common.contract import PadMode
from amfa.factors.base import internal_auth
from amfa.factors.voice import models as vm
from amfa.factors.voice import service
from tests.factors.voice.test_identification import embed

QUALITY = {"voicedS": 3.0, "clippedRatio": 0.0, "snrDb": 25.0}
VERSION = "fake-ecapa"


class FakeSpeaker:
    version = VERSION

    def embed(self, wav):
        return embed(int(round(float(wav[0]))), int(round((float(wav[0]) - round(float(wav[0]))) * 100)))


class FakePad:
    version = "fake-pad"

    def __init__(self):
        self.score_value = 0.0

    def score(self, wav):
        return self.score_value


@pytest.fixture
def env(monkeypatch):
    pad = FakePad()
    monkeypatch.setattr(vm, "speaker", lambda: FakeSpeaker())
    monkeypatch.setattr(vm, "pad", lambda: pad)
    monkeypatch.setattr(service, "decode_audio",
                        lambda data: (np.full(16000, float(data), dtype=np.float32), "d", dict(QUALITY)))
    service.app.dependency_overrides[internal_auth] = lambda: None
    yield TestClient(service.app), pad
    service.app.dependency_overrides.clear()


def enrol(tenant, users, model=VERSION):
    for user, code in users.items():
        service.store.put(tenant, user, {"voiceprint": embed(code).tolist(), "model": model}, meta={})


def probe(client, tenant, code, **kw):
    body = {"tenantId": tenant, "payload": {"audio": str(code)}, **kw}
    return client.post("/factor/identify", json=body).json()


def test_names_the_enrolled_user(env):
    client, _ = env
    enrol("tn_a1", {f"cu_{c}": c for c in range(1, 6)})
    r = probe(client, "tn_a1", 3.07)
    assert r["decision"] == "IDENTIFIED" and r["identifiedUserId"] == "cu_3"
    assert r["gallerySize"] == 5
    assert (r["candidates"][0]["userId"], r["candidates"][0]["rank"]) == ("cu_3", 1)
    assert r["candidates"][0]["score"] == pytest.approx(r["top1Score"], abs=1e-4)


def test_a_stranger_is_not_matched_to_the_nearest_user(env):
    client, _ = env
    enrol("tn_a2", {f"cu_{c}": c for c in range(1, 6)})
    r = probe(client, "tn_a2", 77)
    assert r["decision"] == "REJECTED_NO_MATCH" and r["identifiedUserId"] is None


def test_excluding_the_speaker_themselves_finds_only_other_accounts(env):
    client, _ = env
    enrol("tn_a3", {"cu_1": 1, "cu_2": 2})
    assert probe(client, "tn_a3", 1.02)["identifiedUserId"] == "cu_1"
    other = probe(client, "tn_a3", 1.02, excludeUserId="cu_1")
    assert other["decision"] == "REJECTED_NO_MATCH" and other["gallerySize"] == 1


def test_one_voice_under_two_accounts_is_reported_as_ambiguous(env):
    client, _ = env
    enrol("tn_a4", {"cu_a": 8, "cu_b": 8})
    r = probe(client, "tn_a4", 8.03)
    assert r["decision"] == "REJECTED_AMBIGUOUS" and r["identifiedUserId"] is None
    assert {c["userId"] for c in r["candidates"]} == {"cu_a", "cu_b"}


def test_other_tenants_are_never_searched(env):
    client, _ = env
    enrol("tn_a5", {"cu_1": 1})
    r = probe(client, "tn_a6", 1.0)
    assert r["decision"] == "REJECTED_EMPTY_GALLERY" and r["gallerySize"] == 0


def test_templates_from_another_model_version_are_left_out(env):
    client, _ = env
    enrol("tn_a7", {"cu_1": 1}, model="older-model")
    enrol("tn_a7", {"cu_2": 2})
    r = probe(client, "tn_a7", 1.0)
    assert r["gallerySize"] == 1 and r["decision"] == "REJECTED_NO_MATCH"


def test_a_spoofed_probe_never_reaches_the_gallery(env, monkeypatch):
    client, pad = env
    monkeypatch.setattr(vm, "PAD_MODE", "on")                    # the anti-spoof model deciding, as before
    enrol("tn_a8", {"cu_1": 1})
    pad.score_value = vm.PAD_THRESHOLD - 1
    r = probe(client, "tn_a8", 1.0)
    assert r["decision"] == "REJECTED_SPOOF" and "gallerySize" not in r and "identifiedUserId" not in r
    assert probe(client, "tn_a8", 1.0, padMode=PadMode.ADVISORY.value)["decision"] == "IDENTIFIED"


def test_bad_audio_is_an_input_rejection(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(service, "decode_audio", lambda d: (np.zeros(10), "d", {**QUALITY, "snrDb": 1.0}))
    r = probe(client, "tn_a9", 1.0)
    assert r["decision"] == "REJECTED_INPUT" and r["reason"] == "TOO_NOISY"


def test_the_ident_threshold_is_never_below_the_verification_threshold():
    assert vm.IDENT_THRESHOLD >= vm.MATCH_THRESHOLD and vm.IDENT_MARGIN >= 0


# ---- the anti-spoof model in shadow (AMFA_VOICE_PAD, default) -------------------------------------------------------------
# the score every real voice got on 2026-11-18 (9 of 9 phone recordings, 3 people): far below the line, i.e. "fake"
MEASURED_REAL_VOICE = -11.97


def check(client, tenant, user, code):
    body = {"tenantId": tenant, "userId": user, "sessionId": "s", "transactionId": "t", "challenge": {},
            "padMode": PadMode.ADVISORY.value, "payload": {"audio": str(code)}}
    return client.post("/factor/verify", json=body).json()


def test_the_anti_spoof_model_is_in_shadow_by_default():
    assert vm.PAD_MODE == "shadow"


def test_in_shadow_a_real_voice_it_calls_fake_passes_without_a_warning_and_its_score_is_kept(env):
    client, pad = env
    enrol("tn_v1", {"cu_1": 1})
    pad.score_value = MEASURED_REAL_VOICE
    r = check(client, "tn_v1", "cu_1", 1.0)
    assert r["status"] == "PASSED" and r["failureReason"] is None
    assert r["pad"]["verdict"] == "SHADOW" and r["pad"]["score"] == MEASURED_REAL_VOICE
    assert r["livenessPassed"] is None and r["livenessScore"] == MEASURED_REAL_VOICE


def test_in_shadow_a_terminal_bank_does_not_reject_on_it_either(env):
    client, pad = env
    enrol("tn_v2", {"cu_1": 1})
    pad.score_value = MEASURED_REAL_VOICE
    body = {"tenantId": "tn_v2", "userId": "cu_1", "sessionId": "s", "transactionId": "t", "challenge": {},
            "padMode": PadMode.TERMINAL.value, "payload": {"audio": "1.0"}}
    assert client.post("/factor/verify", json=body).json()["status"] == "PASSED"


def test_in_shadow_a_different_speaker_still_fails(env):
    client, pad = env
    enrol("tn_v3", {"cu_1": 1})
    r = check(client, "tn_v3", "cu_1", 4.0)
    assert r["status"] == "FAILED" and r["failureReason"] == "SPEAKER_IDENTITY_MISMATCH"


def test_switched_on_it_warns_again(env, monkeypatch):
    client, pad = env
    monkeypatch.setattr(vm, "PAD_MODE", "on")
    enrol("tn_v4", {"cu_1": 1})
    pad.score_value = MEASURED_REAL_VOICE
    r = check(client, "tn_v4", "cu_1", 1.0)
    assert r["status"] == "PASSED" and r["failureReason"] == "PAD_ADVISORY_SPOOF_SUSPECTED"
    assert r["pad"]["verdict"] == "SPOOF" and r["livenessPassed"] is False


def test_in_shadow_identification_is_not_rejected_on_it(env):
    client, pad = env
    enrol("tn_v5", {"cu_1": 1})
    pad.score_value = MEASURED_REAL_VOICE
    r = probe(client, "tn_v5", 1.0)
    assert r["decision"] == "IDENTIFIED" and r["pad"]["verdict"] == "SHADOW"


# ---- what the anti-spoof model is given (models.pad_input) -----------------------------------------------------------------

def _speechy(seconds=2.0, level=0.5):
    t = np.arange(int(16000 * seconds)) / 16000
    return (level * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def test_a_quiet_recording_reaches_the_model_at_the_same_level_as_a_loud_one():
    """A phone records quietly; the model scored real speech at a tenth of its volume the same as silence."""
    loud, quiet = vm.pad_input(_speechy(level=0.5)), vm.pad_input(_speechy(level=0.05))
    assert abs(float(np.abs(loud).max()) - vm.PAD_PEAK) < 1e-4 and np.allclose(loud, quiet, atol=1e-3)


def test_silence_at_the_ends_is_cut_off():
    w = np.concatenate([np.zeros(24000, np.float32), _speechy(), np.zeros(16000, np.float32)])
    out = vm.pad_input(w)
    assert len(out) < len(w) and abs(len(out) - len(_speechy()) - 2 * 1600) <= 400 + 2 * 1600


def test_silence_is_left_as_it_is():
    z = np.zeros(32000, np.float32)
    assert np.array_equal(vm.pad_input(z), z)


def test_the_service_hands_on_the_raw_recording_only_the_anti_spoof_model_rescales_it(env):
    """pad_input runs inside the anti-spoof model: the recording the voiceprint is made from is unchanged."""
    client, pad = env
    seen = []
    pad.score = lambda wav: (seen.append(wav), 0.0)[1]
    enrol("tn_v6", {"cu_1": 1})
    assert check(client, "tn_v6", "cu_1", 1.0)["status"] == "PASSED"
    assert float(seen[0][0]) == 1.0                                       # the scripted audio, not rescaled
