import numpy as np

from src.extractors import SpeakerVerifier
from src.pipeline import VoiceBiometricPipeline
from tests.test_ecapa import MockExtractor


class MockPADDetector:
    """Mock PAD detector with configurable decision."""
    def __init__(self, should_pass: bool = True):
        self.should_pass = should_pass

    def predict(self, audio_input, threshold=None):
        if self.should_pass:
            return {
                "rawnet2_score": -0.01,
                "predicted_label": "BONAFIDE",
                "prob_bonafide": 0.99,
                "prob_spoof": 0.01,
                "inference_time_ms": 22.0
            }
        else:
            return {
                "rawnet2_score": -8.50,
                "predicted_label": "SPOOF",
                "prob_bonafide": 0.0002,
                "prob_spoof": 0.9998,
                "inference_time_ms": 22.0
            }


def test_pipeline_spoof_rejection():
    # With pad_mode="enforce", when PAD detects SPOOF the pipeline must reject early and bypass speaker verification
    mock_pad = MockPADDetector(should_pass=False)
    mock_sv = SpeakerVerifier(extractor=MockExtractor(), threshold=0.25)

    pipeline = VoiceBiometricPipeline(rawnet2_detector=mock_pad, speaker_verifier=mock_sv, pad_mode="enforce")
    voiceprint = np.ones(192) / np.sqrt(192)

    result = pipeline.authenticate_factor(
        enrolled_voiceprint=voiceprint,
        test_audio=np.zeros(16000)
    )

    assert result["voice_factor_passed"] is False
    assert result["factor_status"] == "REJECTED"
    assert result["rejection_reason"] == "PRESENTATION_ATTACK_DETECTED"
    assert result["stage_failed"] == "STAGE_1_PAD"
    assert result["verification_result"] is None  # Bypassed!


def test_pipeline_bonafide_and_speaker_match():
    # When PAD is BONAFIDE and speaker matches, voice factor passes
    mock_pad = MockPADDetector(should_pass=True)
    mock_sv = SpeakerVerifier(extractor=MockExtractor(), threshold=0.25)

    pipeline = VoiceBiometricPipeline(rawnet2_detector=mock_pad, speaker_verifier=mock_sv)
    voiceprint = np.ones(192) / np.sqrt(192)

    result = pipeline.authenticate_factor(
        enrolled_voiceprint=voiceprint,
        test_audio=np.ones(16000)
    )

    assert result["voice_factor_passed"] is True
    assert result["factor_status"] == "PASSED"
    assert result["rejection_reason"] is None
    # Crucial policy notice verification
    assert "Adaptive MFA" in result["policy_notice"]
    assert "does not by itself constitute final user authentication" in result["policy_notice"]


def test_pad_mode_defaults_to_shadow_and_rejects_unknown_values():
    import pytest
    assert VoiceBiometricPipeline(speaker_verifier=SpeakerVerifier(extractor=MockExtractor())).pad_mode == "shadow"
    with pytest.raises(ValueError):
        VoiceBiometricPipeline(speaker_verifier=SpeakerVerifier(extractor=MockExtractor()), pad_mode="off")


def test_in_shadow_a_spoof_verdict_is_reported_but_does_not_reject():
    """RawNet2 called real phone voices SPOOF; in shadow the speaker match decides and the verdict is kept."""
    pipeline = VoiceBiometricPipeline(rawnet2_detector=MockPADDetector(should_pass=False),
                                      speaker_verifier=SpeakerVerifier(extractor=MockExtractor(), threshold=0.25))
    result = pipeline.authenticate_factor(enrolled_voiceprint=np.ones(192) / np.sqrt(192), test_audio=np.ones(16000))
    assert result["voice_factor_passed"] is True and result["factor_status"] == "PASSED"
    assert result["pad_result"]["predicted_label"] == "SPOOF" and result["pad_result"]["pad_mode"] == "shadow"
    assert result["verification_result"] is not None
