"""
Voice Biometric Pipeline Demonstration using Synthetic Audio.

DISCLAIMER:
The synthetic demo verifies software integration and data flow only.
It must NOT claim that synthetic audio produces a valid biometric authentication result.
Synthetic audio is only for testing the software pipeline and does not constitute
meaningful biometric authentication validation.
"""

import math
import numpy as np

from src.extractors import SpeakerVerifier
from src.pipeline import VoiceBiometricPipeline


def generate_synthetic_tone(frequency: float, duration_sec: float = 3.0, sample_rate: int = 16000) -> np.ndarray:
    """Generate a simple synthetic sine wave for pipeline connectivity testing."""
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    waveform = 0.5 * np.sin(2 * math.pi * frequency * t)
    # Add subtle harmonic
    waveform += 0.2 * np.sin(2 * math.pi * (frequency * 2) * t)
    return waveform.astype(np.float32)


def main():
    print("=" * 80)
    print("  VOICE BIOMETRIC AUTHENTICATION: PIPELINE INTEGRATION DEMO")
    print("=" * 80)
    print("\n[DISCLAIMER]")
    print("  This demonstration uses synthetic sine waveforms exclusively.")
    print("  The synthetic demo verifies software integration and data flow only.")
    print("  It must NOT claim that synthetic audio produces a valid biometric authentication result.")
    print("  Synthetic audio is only for testing the software pipeline and does not constitute")
    print("  meaningful biometric authentication validation.\n")

    # 1. Initialize Speaker Verifier
    print("[1/3] Initializing ECAPA-TDNN Speaker Verifier...")
    verifier = SpeakerVerifier(threshold=0.25)
    print("  Speaker Verifier initialized successfully.\n")

    # 2. Simulate Enrollment
    print("[2/3] Simulating Enrollment Phase...")
    enrollment_sample_1 = generate_synthetic_tone(frequency=220.0, duration_sec=2.5)
    enrollment_sample_2 = generate_synthetic_tone(frequency=225.0, duration_sec=2.5)

    enrollment_result = verifier.enroll([enrollment_sample_1, enrollment_sample_2])
    voiceprint = enrollment_result["voiceprint"]
    print(f"  Enrolled Voiceprint Dimension : {enrollment_result['embedding_dim']}")
    print(f"  Utterances Combined           : {enrollment_result['sample_count']}")
    print(f"  Total Enrollment Extraction   : {enrollment_result['total_enrollment_time_ms']:.2f} ms\n")

    # 3. Simulate Verification Pipeline
    print("[3/3] Simulating Verification Flow...")
    pipeline = VoiceBiometricPipeline(speaker_verifier=verifier, verification_threshold=0.25)

    # Test Sample A (Similar pitch to enrollment)
    test_sample_a = generate_synthetic_tone(frequency=222.0, duration_sec=2.5)
    result_a = pipeline.authenticate_factor(enrolled_voiceprint=voiceprint, test_audio=test_sample_a)

    print("  Trial A (Synthetic Tone ~222 Hz):")
    print(f"    - Factor Status      : {result_a['factor_status']}")
    print(f"    - Voice Factor Passed: {result_a['voice_factor_passed']}")
    print(f"    - Cosine Similarity  : {result_a['verification_result']['cosine_similarity']:.4f}")
    print(f"    - Threshold          : {result_a['verification_result']['threshold']}")
    print(f"    - Pipeline Latency   : {result_a['total_pipeline_latency_ms']:.2f} ms")
    print(f"    - Policy Notice      : {result_a['policy_notice']}\n")

    # Test Sample B (Divergent frequency)
    test_sample_b = generate_synthetic_tone(frequency=880.0, duration_sec=2.5)
    result_b = pipeline.authenticate_factor(enrolled_voiceprint=voiceprint, test_audio=test_sample_b)

    print("  Trial B (Synthetic Tone ~880 Hz):")
    print(f"    - Factor Status      : {result_b['factor_status']}")
    print(f"    - Voice Factor Passed: {result_b['voice_factor_passed']}")
    print(f"    - Cosine Similarity  : {result_b['verification_result']['cosine_similarity']:.4f}")
    print(f"    - Rejection Reason   : {result_b['rejection_reason']}")
    print(f"    - Pipeline Latency   : {result_b['total_pipeline_latency_ms']:.2f} ms")
    print(f"    - Policy Notice      : {result_b['policy_notice']}\n")

    print("=" * 80)
    print("  Integration demonstration completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    main()
