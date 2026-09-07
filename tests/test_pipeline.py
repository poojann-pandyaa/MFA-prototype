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
    # When PAD detects SPOOF, the pipeline must reject early and bypass speaker verification
    mock_pad = MockPADDetector(should_pass=False)
    mock_sv = SpeakerVerifier(extractor=MockExtractor(), threshold=0.25)

    pipeline = VoiceBiometricPipeline(rawnet2_detector=mock_pad, speaker_verifier=mock_sv)
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
