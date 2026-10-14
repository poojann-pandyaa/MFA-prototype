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


def test_a_spoofed_probe_never_reaches_the_gallery(env):
    client, pad = env
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
