import numpy as np
from src.extractors import BaseSpeakerExtractor, SpeakerVerifier


class MockExtractor(BaseSpeakerExtractor):
    """Mock extractor producing deterministic 192-dim embeddings for testing."""
    @property
    def model_name(self) -> str:
        return "Mock-ECAPA-TDNN"

    def extract_embedding(self, audio_input):
        # Generate predictable embedding based on input value
        if isinstance(audio_input, np.ndarray):
            mean_val = float(np.mean(audio_input))
        else:
            mean_val = 0.5
        vec = np.ones(192, dtype=np.float32) * mean_val
        vec = vec / (np.linalg.norm(vec) + 1e-12)
        return vec, 0.035


def test_speaker_verifier_enrollment_and_verification():
    mock_ext = MockExtractor()
    verifier = SpeakerVerifier(extractor=mock_ext, threshold=0.25)

    sample1 = np.ones(16000, dtype=np.float32)
    sample2 = np.ones(16000, dtype=np.float32) * 1.1

    enroll_res = verifier.enroll([sample1, sample2])
    assert enroll_res["sample_count"] == 2
    assert enroll_res["embedding_dim"] == 192
    voiceprint = enroll_res["voiceprint"]
    assert abs(np.linalg.norm(voiceprint) - 1.0) < 1e-5

    # Test Genuine Match (identical vector)
    test_genuine = np.ones(16000, dtype=np.float32)
    ver_res = verifier.verify(enrolled_voiceprint=voiceprint, test_audio=test_genuine)
    assert ver_res["verified"] is True
    assert ver_res["decision"] == "GENUINE"
    assert ver_res["cosine_similarity"] > 0.99
