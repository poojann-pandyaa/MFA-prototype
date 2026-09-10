import numpy as np
import pytest

from src.extractors import BaseSpeakerExtractor, SpeakerVerifier
from src.gallery import SpeakerGallery
from src.metrics import (
    cmc_curve,
    effective_false_match_rate,
    open_set_identification_rates,
    rank_n_accuracy,
)
from src.pipeline import VoiceBiometricPipeline
from tests.test_pipeline import MockPADDetector

EMBEDDING_DIM = 192


class MultiSpeakerMockExtractor(BaseSpeakerExtractor):
    """
    Mock extractor that maps synthetic audio to distinct per-speaker directions.

    The integer part of the waveform's mean encodes speaker identity; the
    fractional part encodes a small within-speaker utterance variation. This
    yields high genuine similarity and near-zero impostor similarity, so
    open-set thresholds and margins can be exercised deterministically.
    """

    @property
    def model_name(self) -> str:
        return "MultiSpeaker-Mock-ECAPA-TDNN"

    def extract_embedding(self, audio_input):
        value = float(np.mean(audio_input))
        speaker_code = int(round(value))
        variation = value - speaker_code

        base = np.random.default_rng(speaker_code).normal(size=EMBEDDING_DIM)
        base = base / np.linalg.norm(base)

        if variation != 0.0:
            jitter = np.random.default_rng(9000 + int(round(abs(variation) * 1000))).normal(
                size=EMBEDDING_DIM
            )
            jitter = jitter / np.linalg.norm(jitter)
            base = base + (variation * 2.0) * jitter
            base = base / np.linalg.norm(base)

        return base, 0.035


def speaker_audio(speaker_code: int, utterance: int = 0) -> np.ndarray:
    """Synthesize a mock waveform for a given speaker and utterance index."""
    return np.full(16000, speaker_code + utterance * 0.01, dtype=np.float32)


@pytest.fixture
def verifier():
    return SpeakerVerifier(
        extractor=MultiSpeakerMockExtractor(),
        threshold=0.25,
        identification_threshold=0.30,
        identification_margin=0.05,
    )


@pytest.fixture
def gallery(verifier):
    """Gallery of 5 enrolled speakers (codes 1..5), 2 enrollment utterances each."""
    g = SpeakerGallery(embedding_dim=EMBEDDING_DIM)
    for code in range(1, 6):
        enrollment = verifier.enroll([speaker_audio(code, 0), speaker_audio(code, 1)])
        g.enroll_speaker(f"user_{code}", enrollment["voiceprint"])
    return g


# ---------------------------------------------------------------------------
# Core 1:N identification behaviour
# ---------------------------------------------------------------------------

def test_identifies_correct_speaker_from_gallery(verifier, gallery):
    # An enrolled speaker presents an unseen utterance; the system must name them.
    result = verifier.identify(gallery, speaker_audio(3, utterance=7))

    assert result["identified"] is True
    assert result["identified_speaker_id"] == "user_3"
    assert result["decision"] == "IDENTIFIED"
    assert result["rejection_reason"] is None
    assert result["gallery_size"] == 5
    assert result["top1_score"] >= result["threshold"]


def test_every_enrolled_speaker_is_identified_at_rank_1(verifier, gallery):
    for code in range(1, 6):
        result = verifier.identify(gallery, speaker_audio(code, utterance=9))
        assert result["identified_speaker_id"] == f"user_{code}", (
            f"speaker {code} misidentified as {result['identified_speaker_id']}"
        )


def test_unenrolled_speaker_is_rejected_not_nearest_neighbour(verifier, gallery):
    # Speaker 42 was never enrolled. A pure nearest-neighbour search would still
    # return whichever enrolled user scores highest -- open-set rejection must not.
    result = verifier.identify(gallery, speaker_audio(42))

    assert result["identified"] is False
    assert result["identified_speaker_id"] is None
    assert result["decision"] == "REJECTED_NO_MATCH"
    assert result["rejection_reason"] == "NO_ENROLLED_SPEAKER_ABOVE_THRESHOLD"
    # The search still ranks candidates; the system simply refuses to accept one.
    assert len(result["candidates"]) > 0
    assert result["top1_score"] < result["threshold"]


def test_ambiguous_match_between_two_enrolments_is_rejected(verifier):
    # Two identities enrolled with the same voiceprint => zero top-1/top-2 margin.
    g = SpeakerGallery(embedding_dim=EMBEDDING_DIM)
    shared = verifier.enroll([speaker_audio(8, 0)])["voiceprint"]
    g.enroll_speaker("user_twin_a", shared)
    g.enroll_speaker("user_twin_b", shared)

    result = verifier.identify(g, speaker_audio(8, utterance=3))

    assert result["identified"] is False
    assert result["decision"] == "REJECTED_AMBIGUOUS"
    assert result["rejection_reason"] == "INSUFFICIENT_MARGIN_BETWEEN_TOP_CANDIDATES"
    assert result["score_margin"] < result["margin"]
    # The probe DID clear the absolute threshold -- only the margin test failed.
    assert result["top1_score"] >= result["threshold"]


def test_top_k_cannot_weaken_the_decision(verifier):
    # Regression: top_k is a REPORTING parameter. Fetching only one candidate must
    # not skip the top-1/top-2 margin test, which would silently accept an
    # ambiguous probe that top_k=5 correctly rejects.
    g = SpeakerGallery(embedding_dim=EMBEDDING_DIM)
    shared = verifier.enroll([speaker_audio(8, 0)])["voiceprint"]
    g.enroll_speaker("user_twin_a", shared)
    g.enroll_speaker("user_twin_b", shared)

    decisions = {}
    for k in (1, 2, 5):
        result = verifier.identify(g, speaker_audio(8, utterance=3), top_k=k)
        decisions[k] = result["decision"]
        # Only the reported candidate list is truncated.
        assert len(result["candidates"]) == min(k, len(g))
        # The margin is always computed, regardless of top_k.
        assert result["score_margin"] is not None

    assert set(decisions.values()) == {"REJECTED_AMBIGUOUS"}, decisions


def test_top_k_does_not_change_identified_speaker(verifier, gallery):
    baseline = verifier.identify(gallery, speaker_audio(3, utterance=7), top_k=5)
    for k in (1, 2, 3, 10):
        result = verifier.identify(gallery, speaker_audio(3, utterance=7), top_k=k)
        assert result["identified_speaker_id"] == baseline["identified_speaker_id"]
        assert result["decision"] == baseline["decision"]
        assert result["top1_score"] == pytest.approx(baseline["top1_score"])
        assert result["top2_score"] == pytest.approx(baseline["top2_score"])


def test_empty_gallery_is_rejected(verifier):
    result = verifier.identify(SpeakerGallery(embedding_dim=EMBEDDING_DIM), speaker_audio(1))

    assert result["identified"] is False
    assert result["decision"] == "REJECTED_EMPTY_GALLERY"
    assert result["rejection_reason"] == "NO_ENROLLED_IDENTITIES"
    assert result["candidates"] == []
    assert result["top1_score"] is None


def test_single_speaker_gallery_has_no_margin_condition(verifier):
    # With N=1 there is no top-2 score, so the margin test cannot apply.
    g = SpeakerGallery(embedding_dim=EMBEDDING_DIM)
    g.enroll_speaker("user_solo", verifier.enroll([speaker_audio(4, 0)])["voiceprint"])

    result = verifier.identify(g, speaker_audio(4, utterance=2))
    assert result["identified"] is True
    assert result["top2_score"] is None
    assert result["score_margin"] is None


def test_threshold_and_margin_overrides_are_honoured(verifier, gallery):
    # An impossible threshold must reject a probe that otherwise identifies cleanly.
    strict = verifier.identify(gallery, speaker_audio(2, utterance=4), threshold=0.999)
    assert strict["identified"] is False
    assert strict["decision"] == "REJECTED_NO_MATCH"

    # An impossible margin must reject on ambiguity instead.
    wide = verifier.identify(gallery, speaker_audio(2, utterance=4), margin=1.99)
    assert wide["identified"] is False
    assert wide["decision"] == "REJECTED_AMBIGUOUS"


# ---------------------------------------------------------------------------
# Pipeline integration: PAD stage still guards the gallery search
# ---------------------------------------------------------------------------

def test_pipeline_spoof_rejection_bypasses_gallery_search(verifier, gallery):
    pipeline = VoiceBiometricPipeline(
        rawnet2_detector=MockPADDetector(should_pass=False),
        speaker_verifier=verifier,
    )

    result = pipeline.identify_factor(gallery=gallery, test_audio=speaker_audio(3))

    assert result["voice_factor_passed"] is False
    assert result["rejection_reason"] == "PRESENTATION_ATTACK_DETECTED"
    assert result["stage_failed"] == "STAGE_1_PAD"
    assert result["identification_result"] is None  # Gallery never searched
    assert result["identified_speaker_id"] is None


def test_pipeline_identifies_bonafide_enrolled_speaker(verifier, gallery):
    pipeline = VoiceBiometricPipeline(
        rawnet2_detector=MockPADDetector(should_pass=True),
        speaker_verifier=verifier,
    )

    result = pipeline.identify_factor(gallery=gallery, test_audio=speaker_audio(5, utterance=6))

    assert result["voice_factor_passed"] is True
    assert result["factor_status"] == "PASSED"
    assert result["identified_speaker_id"] == "user_5"
    assert result["pad_result"]["predicted_label"] == "BONAFIDE"
    assert "does not by itself constitute final user authentication" in result["policy_notice"]


def test_pipeline_rejects_bonafide_but_unenrolled_speaker(verifier, gallery):
    # Genuine human speech from someone who is simply not enrolled.
    pipeline = VoiceBiometricPipeline(
        rawnet2_detector=MockPADDetector(should_pass=True),
        speaker_verifier=verifier,
    )

    result = pipeline.identify_factor(gallery=gallery, test_audio=speaker_audio(77))

    assert result["voice_factor_passed"] is False
    assert result["factor_status"] == "REJECTED"
    assert result["identified_speaker_id"] is None
    assert result["stage_failed"] == "STAGE_2_SPEAKER_IDENTIFICATION"
    assert result["pad_result"]["predicted_label"] == "BONAFIDE"


# ---------------------------------------------------------------------------
# Backward compatibility: the 1:1 path must be untouched
# ---------------------------------------------------------------------------

def test_one_to_one_verification_still_works(verifier):
    voiceprint = verifier.enroll([speaker_audio(1, 0), speaker_audio(1, 1)])["voiceprint"]

    genuine = verifier.verify(voiceprint, speaker_audio(1, utterance=5))
    assert genuine["verified"] is True
    assert genuine["decision"] == "GENUINE"

    impostor = verifier.verify(voiceprint, speaker_audio(2, utterance=5))
    assert impostor["verified"] is False
    assert impostor["decision"] == "IMPOSTOR"


# ---------------------------------------------------------------------------
# Identification metrics
# ---------------------------------------------------------------------------

def test_rank_n_accuracy():
    ranked = [["a", "b", "c"], ["b", "a", "c"], ["c", "b", "a"]]
    truth = ["a", "a", "a"]

    assert rank_n_accuracy(ranked, truth, n=1) == pytest.approx(1 / 3)
    assert rank_n_accuracy(ranked, truth, n=2) == pytest.approx(2 / 3)
    assert rank_n_accuracy(ranked, truth, n=3) == pytest.approx(1.0)
    assert rank_n_accuracy([], [], n=1) == 0.0


def test_cmc_curve_is_monotonically_non_decreasing():
    ranked = [["a", "b", "c"], ["b", "a", "c"], ["c", "b", "a"]]
    curve = cmc_curve(ranked, ["a", "a", "a"])

    assert len(curve) == 3
    assert curve == sorted(curve)
    assert curve[0] == pytest.approx(1 / 3)
    assert curve[-1] == pytest.approx(1.0)


def test_rank_n_accuracy_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="equal length"):
        rank_n_accuracy([["a"]], ["a", "b"], n=1)


def test_open_set_rates_separate_misidentification_from_rejection():
    results = [
        {"identified": True, "identified_speaker_id": "a"},    # genuine, correct
        {"identified": True, "identified_speaker_id": "b"},    # genuine, WRONG identity
        {"identified": False, "identified_speaker_id": None},  # genuine, rejected
        {"identified": False, "identified_speaker_id": None},  # impostor, correctly rejected
        {"identified": True, "identified_speaker_id": "a"},    # impostor, falsely accepted
    ]
    truth = ["a", "a", "a", None, None]

    rates = open_set_identification_rates(results, truth)

    assert rates["genuine_probes"] == 3
    assert rates["impostor_probes"] == 2
    assert rates["dir"] == pytest.approx(1 / 3)
    assert rates["fnir"] == pytest.approx(2 / 3)
    assert rates["fpir"] == pytest.approx(1 / 2)
    # Accepting a genuine probe under the wrong identity is a failure, not a success.
    assert rates["misidentification_rate"] == pytest.approx(1 / 3)


def test_open_set_rates_on_gallery(verifier, gallery):
    probes = [speaker_audio(c, utterance=8) for c in range(1, 6)] + [speaker_audio(99)]
    truth = [f"user_{c}" for c in range(1, 6)] + [None]

    results = [verifier.identify(gallery, p) for p in probes]
    rates = open_set_identification_rates(results, truth)

    assert rates["dir"] == pytest.approx(1.0)
    assert rates["fpir"] == pytest.approx(0.0)
    assert rates["misidentification_rate"] == pytest.approx(0.0)


def test_effective_false_match_rate_grows_with_gallery_size():
    assert effective_false_match_rate(0.01, 1) == pytest.approx(0.01)
    assert effective_false_match_rate(0.0, 500) == pytest.approx(0.0)

    # A 1% 1:1 FAR becomes roughly 22% per attempt across a 25-speaker gallery.
    assert effective_false_match_rate(0.01, 25) == pytest.approx(0.2222, abs=1e-3)
    assert effective_false_match_rate(0.01, 100) > effective_false_match_rate(0.01, 25)

    with pytest.raises(ValueError):
        effective_false_match_rate(1.5, 10)
