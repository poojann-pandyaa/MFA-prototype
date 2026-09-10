"""
1:N Open-Set Speaker Identification Demonstration using Synthetic Audio.

DISCLAIMER:
The synthetic demo verifies software integration and data flow only.
It must NOT claim that synthetic audio produces a valid biometric authentication result.
Synthetic audio is only for testing the software pipeline and does not constitute
meaningful biometric authentication validation. In particular, the identification
outcomes shown below are NOT a benchmark of 1:N accuracy -- see README Section 11.
"""

import math
import numpy as np

from src.extractors import SpeakerVerifier
from src.gallery import SpeakerGallery
from src.pipeline import VoiceBiometricPipeline


def generate_synthetic_tone(frequency: float, duration_sec: float = 3.0, sample_rate: int = 16000) -> np.ndarray:
    """Generate a simple synthetic sine wave for pipeline connectivity testing."""
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    waveform = 0.5 * np.sin(2 * math.pi * frequency * t)
    # Add subtle harmonic
    waveform += 0.2 * np.sin(2 * math.pi * (frequency * 2) * t)
    return waveform.astype(np.float32)


# Synthetic "speakers", each represented by a distinct base frequency.
ENROLLED_SPEAKERS = {
    "user_alice": 180.0,
    "user_bob": 260.0,
    "user_carol": 340.0,
    "user_dave": 420.0,
}
UNENROLLED_FREQUENCY = 700.0


def main():
    print("=" * 80)
    print("  VOICE BIOMETRIC AUTHENTICATION: 1:N OPEN-SET IDENTIFICATION DEMO")
    print("=" * 80)
    print("\n[DISCLAIMER]")
    print("  This demonstration uses synthetic sine waveforms exclusively.")
    print("  It verifies software integration and data flow only, and does NOT")
    print("  constitute meaningful biometric validation or a 1:N accuracy benchmark.\n")

    # 1. Initialize verifier
    print("[1/4] Initializing ECAPA-TDNN Speaker Verifier...")
    verifier = SpeakerVerifier(
        threshold=0.25,
        identification_threshold=0.30,
        identification_margin=0.05,
    )
    print(f"  1:1 verification threshold : {verifier.threshold}")
    print(f"  1:N identification threshold: {verifier.identification_threshold}")
    print(f"  1:N ranking margin          : {verifier.identification_margin}\n")

    # 2. Build the gallery of N enrolled identities
    print(f"[2/4] Enrolling {len(ENROLLED_SPEAKERS)} identities into the gallery...")
    gallery = SpeakerGallery()
    for speaker_id, base_freq in ENROLLED_SPEAKERS.items():
        recordings = [
            generate_synthetic_tone(frequency=base_freq, duration_sec=2.5),
            generate_synthetic_tone(frequency=base_freq + 5.0, duration_sec=2.5),
        ]
        enrollment = verifier.enroll(recordings)
        gallery.enroll_speaker(
            speaker_id,
            enrollment["voiceprint"],
            metadata={"enrollment_samples": enrollment["sample_count"]},
        )
        print(f"  Enrolled {speaker_id:<12} ({enrollment['sample_count']} utterances)")
    print(f"  Gallery size: N = {len(gallery)}\n")

    pipeline = VoiceBiometricPipeline(speaker_verifier=verifier)

    # 3. Probe with each enrolled speaker -- no identity is claimed
    print("[3/4] Identifying probes from ENROLLED speakers (no identity claimed)...")
    for speaker_id, base_freq in ENROLLED_SPEAKERS.items():
        probe = generate_synthetic_tone(frequency=base_freq + 2.0, duration_sec=2.5)
        result = pipeline.identify_factor(gallery=gallery, test_audio=probe, top_k=3)
        ident = result["identification_result"]

        status = "CORRECT" if result["identified_speaker_id"] == speaker_id else "INCORRECT"
        print(f"\n  Probe from {speaker_id} (~{base_freq + 2.0:.0f} Hz):")
        print(f"    - Decision          : {ident['decision']}")
        print(f"    - Identified as     : {result['identified_speaker_id']}  [{status}]")
        print(f"    - Top-1 score       : {ident['top1_score']:.4f}")
        if ident["top2_score"] is not None:
            print(f"    - Top-2 score       : {ident['top2_score']:.4f} "
                  f"(margin {ident['score_margin']:.4f})")
        ranked = ", ".join(
            f"{c['speaker_id']}={c['cosine_similarity']:.3f}" for c in ident["candidates"]
        )
        print(f"    - Ranked candidates : {ranked}")
        print(f"    - Pipeline latency  : {result['total_pipeline_latency_ms']:.2f} ms")

    # 4. Probe with a speaker who was never enrolled
    print(f"\n[4/4] Identifying a probe from an UNENROLLED speaker (~{UNENROLLED_FREQUENCY:.0f} Hz)...")
    print("      A plain nearest-neighbour search would still return a closest match.")
    print("      Open-set rejection must refuse it instead.\n")

    probe = generate_synthetic_tone(frequency=UNENROLLED_FREQUENCY, duration_sec=2.5)
    result = pipeline.identify_factor(gallery=gallery, test_audio=probe, top_k=3)
    ident = result["identification_result"]

    print(f"  - Voice Factor Passed : {result['voice_factor_passed']}")
    print(f"  - Decision            : {ident['decision']}")
    print(f"  - Identified as       : {result['identified_speaker_id']}")
    print(f"  - Rejection Reason    : {result['rejection_reason']}")
    print(f"  - Top-1 score         : {ident['top1_score']:.4f} "
          f"(threshold {ident['threshold']})")
    nearest = ident["candidates"][0]["speaker_id"] if ident["candidates"] else None
    print(f"  - Nearest neighbour   : {nearest} (returned by search, but NOT accepted)")
    print(f"  - Policy Notice       : {result['policy_notice']}")

    print("\n" + "=" * 80)
    print("  1:N identification demonstration completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    main()
