"""
Voice Biometric Authentication Pipeline for Adaptive MFA.

This module implements the complete 2-stage verification pipeline:

    Voice Input Audio (16 kHz mono)
             │
             ▼
    ┌────────────────────────────────────────┐
    │   RawNet2 Presentation Attack Detection │
    │   (SincConv + ResBlocks + GRU)         │
    └───────────────────┬────────────────────┘
                        │
               Is audio BONAFIDE?
               ├── NO (SPOOF)  ──► [REJECT AUTHENTICATION: Attack Detected]
               │                   (Early exit: prevents unnecessary downstream compute)
               └── YES (BONAFIDE)
                        │
                        ▼
    ┌────────────────────────────────────────┐
    │     ECAPA-TDNN Speaker Verification    │
    │  (speechbrain/spkrec-ecapa-voxceleb)   │
    └───────────────────┬────────────────────┘
                        │
            Matches Enrolled Voiceprint?
            ├── NO (IMPOSTOR) ──► [REJECT AUTHENTICATION: Speaker Mismatch]
            └── YES (GENUINE) ──► [VOICE MFA FACTOR PASSES]

IMPORTANT ARCHITECTURAL NOTE:
src/pipeline.py orchestrates the existing RawNet2 and ECAPA-TDNN components
without duplicating, modifying, or reimplementing either model.

IMPORTANT SECURITY / POLICY DISTINCTION:
A successful voice factor only indicates that the voice authentication factor passed;
it does not by itself constitute final user authentication or authorization.
The broader Adaptive MFA system remains responsible for the final authentication/authorization decision.
"""

import time
from pathlib import Path
from typing import Dict, List, Optional, Union
import numpy as np
import torch

from src.anti_spoofing import RawNet2Detector
from src.extractors import ECAPATDNNExtractor, SpeakerVerifier
from src.gallery import SpeakerGallery


class VoiceBiometricPipeline:
    """
    Unified Voice Authentication Pipeline orchestrating RawNet2 Presentation
    Attack Detection followed by ECAPA-TDNN Speaker Verification.
    """
    def __init__(
        self,
        rawnet2_detector: Optional[RawNet2Detector] = None,
        speaker_verifier: Optional[SpeakerVerifier] = None,
        rawnet2_checkpoint: Optional[Union[str, Path]] = None,
        pad_threshold: float = -0.69314718,  # log(0.5)
        verification_threshold: float = 0.25
    ):
        """
        Initialize the 2-stage voice authentication pipeline.

        Args:
            rawnet2_detector: Pre-initialized RawNet2Detector instance.
            speaker_verifier: Pre-initialized SpeakerVerifier instance.
            rawnet2_checkpoint: Path to RawNet2 .pth weights (if detector not pre-initialized).
            pad_threshold: BONAFIDE log-probability threshold for RawNet2.
            verification_threshold: Cosine similarity threshold for ECAPA-TDNN.
        """
        if rawnet2_detector is not None:
            self.pad_detector = rawnet2_detector
        elif rawnet2_checkpoint is not None:
            self.pad_detector = RawNet2Detector(
                checkpoint_path=rawnet2_checkpoint,
                decision_threshold=pad_threshold
            )
        else:
            self.pad_detector = None

        if speaker_verifier is not None:
            self.verifier = speaker_verifier
        else:
            self.verifier = SpeakerVerifier(threshold=verification_threshold)

        self.pad_threshold = pad_threshold
        self.verification_threshold = verification_threshold

    def authenticate_factor(
        self,
        enrolled_voiceprint: np.ndarray,
        test_audio: Union[str, Path, np.ndarray],
        pad_threshold: Optional[float] = None,
        verification_threshold: Optional[float] = None
    ) -> Dict[str, Union[str, bool, float, Dict]]:
        """
        Execute 2-stage voice factor authentication.

        Stage 1: Presentation Attack Detection (RawNet2).
                 If SPOOF: Immediately reject factor.
        Stage 2: Speaker Verification (ECAPA-TDNN).
                 If GENUINE: Voice factor passes.
                 If IMPOSTOR: Reject factor.

        Args:
            enrolled_voiceprint: 192-dim reference vector for enrolled identity.
            test_audio: 16 kHz audio recording path or 1D waveform array.
            pad_threshold: Optional override for PAD threshold.
            verification_threshold: Optional override for verification threshold.

        Returns:
            Dict containing execution summary, step outcomes, and factor decision:
            - voice_factor_passed: bool
            - factor_status: 'PASSED' or 'REJECTED'
            - rejection_reason: None or specific failure cause
            - pad_result: RawNet2 detailed metrics (or skipped if detector absent)
            - verification_result: ECAPA detailed metrics (or skipped if spoofed)
            - total_pipeline_latency_ms: cumulative time in ms
            - disclaimer: reminder that voice factor pass != full user authorization
        """
        t0 = time.perf_counter()
        thr_pad = self.pad_threshold if pad_threshold is None else pad_threshold
        thr_ver = self.verification_threshold if verification_threshold is None else verification_threshold

        # -------------------------------------------------------------
        # Stage 1: Presentation Attack Detection (RawNet2)
        # -------------------------------------------------------------
        pad_result = None
        if self.pad_detector is not None:
            pad_result = self.pad_detector.predict(test_audio, threshold=thr_pad)
            if pad_result["predicted_label"] == "SPOOF":
                total_latency_ms = (time.perf_counter() - t0) * 1000.0
                return {
                    "voice_factor_passed": False,
                    "factor_status": "REJECTED",
                    "rejection_reason": "PRESENTATION_ATTACK_DETECTED",
                    "stage_failed": "STAGE_1_PAD",
                    "pad_result": pad_result,
                    "verification_result": None,
                    "total_pipeline_latency_ms": total_latency_ms,
                    "policy_notice": (
                        "Voice factor REJECTED due to presentation attack. "
                        "Speaker verification was bypassed."
                    )
                }

        # -------------------------------------------------------------
        # Stage 2: Speaker Verification (ECAPA-TDNN)
        # -------------------------------------------------------------
        ver_result = self.verifier.verify(
            enrolled_voiceprint=enrolled_voiceprint,
            test_audio=test_audio,
            threshold=thr_ver
        )

        total_latency_ms = (time.perf_counter() - t0) * 1000.0
        voice_factor_passed = ver_result["verified"]

        if voice_factor_passed:
            factor_status = "PASSED"
            rejection_reason = None
            stage_failed = None
            policy_notice = (
                "A successful voice factor only indicates that the voice authentication "
                "factor passed; it does not by itself constitute final user authentication "
                "or authorization. Final decision rests with the Adaptive MFA engine."
            )
        else:
            factor_status = "REJECTED"
            rejection_reason = "SPEAKER_IDENTITY_MISMATCH"
            stage_failed = "STAGE_2_SPEAKER_VERIFICATION"
            policy_notice = "Voice factor REJECTED due to speaker identity mismatch."

        return {
            "voice_factor_passed": voice_factor_passed,
            "factor_status": factor_status,
            "rejection_reason": rejection_reason,
            "stage_failed": stage_failed,
            "pad_result": pad_result,
            "verification_result": ver_result,
            "total_pipeline_latency_ms": total_latency_ms,
            "policy_notice": policy_notice
        }

    def identify_factor(
        self,
        gallery: SpeakerGallery,
        test_audio: Union[str, Path, np.ndarray],
        pad_threshold: Optional[float] = None,
        identification_threshold: Optional[float] = None,
        identification_margin: Optional[float] = None,
        top_k: int = 5
    ) -> Dict[str, Union[str, bool, float, Dict, None]]:
        """
        Execute 2-stage voice factor authentication in 1:N OPEN-SET IDENTIFICATION mode.

        This is the 1:N counterpart of authenticate_factor(). No identity is claimed
        in advance: the probe is searched against all N enrolled voiceprints and the
        pipeline answers "which enrolled user is this, if any?".

        Stage 1: Presentation Attack Detection (RawNet2) -- identical to the 1:1 flow.
                 RawNet2 is speaker-agnostic, so it is unaffected by gallery size and
                 still early-exits before any gallery search is performed.
        Stage 2: Open-set identification (ECAPA-TDNN + SpeakerGallery).

        Args:
            gallery: SpeakerGallery of enrolled identities to search.
            test_audio: 16 kHz probe recording path or 1D waveform array.
            pad_threshold: Optional override for the PAD threshold.
            identification_threshold: Optional override for the open-set acceptance threshold.
            identification_margin: Optional override for the top-1/top-2 margin.
            top_k: Number of ranked candidates to report.

        Returns:
            Dict containing:
            - voice_factor_passed: bool
            - factor_status: 'PASSED' or 'REJECTED'
            - identified_speaker_id: matched speaker_id, or None
            - rejection_reason: None or specific failure cause
            - pad_result: RawNet2 detailed metrics (or None if detector absent)
            - identification_result: ECAPA/gallery detail (or None if spoofed)
            - total_pipeline_latency_ms: cumulative time in ms
            - policy_notice: reminder that a voice factor pass != full authorization
        """
        t0 = time.perf_counter()
        thr_pad = self.pad_threshold if pad_threshold is None else pad_threshold

        # -------------------------------------------------------------
        # Stage 1: Presentation Attack Detection (RawNet2)
        # -------------------------------------------------------------
        pad_result = None
        if self.pad_detector is not None:
            pad_result = self.pad_detector.predict(test_audio, threshold=thr_pad)
            if pad_result["predicted_label"] == "SPOOF":
                total_latency_ms = (time.perf_counter() - t0) * 1000.0
                return {
                    "voice_factor_passed": False,
                    "factor_status": "REJECTED",
                    "identified_speaker_id": None,
                    "rejection_reason": "PRESENTATION_ATTACK_DETECTED",
                    "stage_failed": "STAGE_1_PAD",
                    "pad_result": pad_result,
                    "identification_result": None,
                    "total_pipeline_latency_ms": total_latency_ms,
                    "policy_notice": (
                        "Voice factor REJECTED due to presentation attack. "
                        "Gallery search was bypassed."
                    )
                }

        # -------------------------------------------------------------
        # Stage 2: Open-Set Speaker Identification (ECAPA-TDNN over gallery)
        # -------------------------------------------------------------
        ident_result = self.verifier.identify(
            gallery=gallery,
            test_audio=test_audio,
            threshold=identification_threshold,
            margin=identification_margin,
            top_k=top_k
        )

        total_latency_ms = (time.perf_counter() - t0) * 1000.0
        voice_factor_passed = bool(ident_result["identified"])

        if voice_factor_passed:
            factor_status = "PASSED"
            stage_failed = None
            policy_notice = (
                "A successful voice factor only indicates that the voice authentication "
                "factor passed; it does not by itself constitute final user authentication "
                "or authorization. Final decision rests with the Adaptive MFA engine. "
                "An identified speaker_id is a biometric claim, not an authenticated session."
            )
        else:
            factor_status = "REJECTED"
            stage_failed = "STAGE_2_SPEAKER_IDENTIFICATION"
            policy_notice = (
                "Voice factor REJECTED: no enrolled identity was confidently matched."
            )

        return {
            "voice_factor_passed": voice_factor_passed,
            "factor_status": factor_status,
            "identified_speaker_id": ident_result["identified_speaker_id"],
            "rejection_reason": ident_result["rejection_reason"],
            "stage_failed": stage_failed,
            "pad_result": pad_result,
            "identification_result": ident_result,
            "total_pipeline_latency_ms": total_latency_ms,
            "policy_notice": policy_notice
        }
