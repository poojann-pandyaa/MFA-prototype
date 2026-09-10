"""
ECAPA-TDNN Speaker Embedding Extractor and Speaker Verification Module.

Based on SpeechBrain's pretrained ECAPA-TDNN architecture:
speechbrain/spkrec-ecapa-voxceleb

Key specifications:
- Sample rate: 16,000 Hz mono
- Embedding dimension: 192
- Distance metric: Cosine similarity
"""

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import soundfile as sf
import torch

try:
    from speechbrain.inference.speaker import EncoderClassifier
except ImportError:
    from speechbrain.pretrained import EncoderClassifier

from src.extractors.base import BaseSpeakerExtractor
from src.gallery import SpeakerGallery
from src.metrics import compute_cosine_similarity, average_embeddings


class ECAPATDNNExtractor(BaseSpeakerExtractor):
    """
    Speaker Embedding Extractor using SpeechBrain's pretrained ECAPA-TDNN model.
    (speechbrain/spkrec-ecapa-voxceleb).
    """
    def __init__(
        self,
        source_repo: str = "speechbrain/spkrec-ecapa-voxceleb",
        save_dir: Optional[Union[str, Path]] = None,
        device: Optional[str] = None
    ):
        """
        Initialize ECAPA-TDNN model.

        Args:
            source_repo: SpeechBrain model identifier on Hugging Face Hub.
            save_dir: Local directory to cache pretrained model weights.
            device: 'cuda', 'cpu', or None (auto-selects cuda if available).
        """
        self.source_repo = source_repo
        if save_dir is None:
            self.save_dir = Path("pretrained_models") / "speechbrain" / "spkrec-ecapa-voxceleb"
        else:
            self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        run_opts = {"device": self.device}

        print(f"Loading ECAPA-TDNN model from '{self.source_repo}' onto {self.device}...")
        fetch_kwargs = {"run_opts": run_opts}
        try:
            from speechbrain.utils.fetching import LocalStrategy
            fetch_kwargs["local_strategy"] = LocalStrategy.COPY
        except (ImportError, AttributeError):
            pass

        self.classifier = EncoderClassifier.from_hparams(
            source=self.source_repo,
            savedir=str(self.save_dir),
            **fetch_kwargs
        )
        print("ECAPA-TDNN model loaded successfully.")

    @property
    def model_name(self) -> str:
        return f"ECAPA-TDNN ({self.source_repo})"

    def extract_embedding(
        self,
        audio_input: Union[str, Path, np.ndarray, torch.Tensor]
    ) -> Tuple[np.ndarray, float]:
        """
        Extract a 192-dimensional speaker embedding from an audio file or waveform array.

        Args:
            audio_input: File path (str/Path) or 1D waveform array/tensor at 16 kHz.

        Returns:
            Tuple[np.ndarray, float]: (192-dim embedding vector, inference_time_sec)
        """
        start_time = time.perf_counter()

        if isinstance(audio_input, (str, Path)):
            path_str = str(Path(audio_input).resolve())
            signal = self.classifier.load_audio(path_str)
        elif isinstance(audio_input, np.ndarray):
            signal = torch.from_numpy(audio_input.astype(np.float32))
            if signal.ndim > 1:
                signal = torch.mean(signal, dim=-1)
        elif isinstance(audio_input, torch.Tensor):
            signal = audio_input.float()
            if signal.ndim > 1:
                signal = torch.mean(signal, dim=-1)
        else:
            raise TypeError(f"Unsupported audio input type: {type(audio_input)}")

        if signal.dim() == 1:
            signal = signal.unsqueeze(0)

        signal = signal.to(self.device)

        embeddings = self.classifier.encode_batch(signal)
        inference_time = time.perf_counter() - start_time

        embedding_np = embeddings.squeeze().detach().cpu().numpy()
        return embedding_np, float(inference_time)


class SpeakerVerifier:
    """
    Speaker Verification helper wrapping ECAPA-TDNN embedding extraction,
    multi-sample voiceprint enrollment, and cosine similarity thresholding.
    """
    def __init__(
        self,
        extractor: Optional[ECAPATDNNExtractor] = None,
        threshold: float = 0.25,
        identification_threshold: float = 0.30,
        identification_margin: float = 0.05
    ):
        """
        Initialize SpeakerVerifier.

        Args:
            extractor: Pre-initialized ECAPATDNNExtractor instance. If None, initialized automatically.
            threshold: Cosine similarity decision threshold for 1:1 verification
                       (default: 0.25, chosen based on Phase 1 benchmark separation gap:
                       highest impostor = 0.2234, lowest genuine = 0.3105).
            identification_threshold: Acceptance threshold for 1:N open-set identification.
                       Deliberately STRICTER than the 1:1 threshold: an identification
                       attempt draws N scores from the impostor distribution instead of
                       one, so the probability that at least one enrolled identity is
                       falsely matched grows roughly as 1 - (1 - FAR)**N with gallery
                       size. See metrics.effective_false_match_rate().

                       WARNING: 0.30 is an interim value derived from the existing
                       2-speaker Phase 1 benchmark (lowest genuine = 0.3105). It MUST be
                       recalibrated on a multi-speaker evaluation set before any claim
                       about 1:N accuracy is reported.
            identification_margin: Minimum required gap between the top-1 and top-2
                       candidate scores for an identification to be accepted.
        """
        self.extractor = extractor or ECAPATDNNExtractor()
        self.threshold = threshold
        self.identification_threshold = identification_threshold
        self.identification_margin = identification_margin

    def enroll(
        self,
        audio_inputs: List[Union[str, Path, np.ndarray, torch.Tensor]]
    ) -> Dict[str, Union[np.ndarray, float, int]]:
        """
        Build an enrolled speaker reference voiceprint by extracting embeddings
        across multiple enrollment recordings and computing an L2-normalized mean vector.

        Args:
            audio_inputs: List of enrollment recordings (paths or waveform arrays).

        Returns:
            Dict containing:
                - voiceprint: 192-dim unit-normalized numpy vector
                - sample_count: number of enrollment utterances combined
                - total_enrollment_time_ms: total extraction latency in ms
        """
        if not audio_inputs:
            raise ValueError("audio_inputs must contain at least one enrollment recording.")

        embeddings = []
        total_time_s = 0.0

        for inp in audio_inputs:
            emb, t_s = self.extractor.extract_embedding(inp)
            embeddings.append(emb)
            total_time_s += t_s

        voiceprint = average_embeddings(embeddings, normalize=True)
        return {
            "voiceprint": voiceprint,
            "sample_count": len(audio_inputs),
            "total_enrollment_time_ms": total_time_s * 1000.0,
            "embedding_dim": int(voiceprint.shape[-1])
        }

    def verify(
        self,
        enrolled_voiceprint: np.ndarray,
        test_audio: Union[str, Path, np.ndarray, torch.Tensor],
        threshold: Optional[float] = None
    ) -> Dict[str, Union[bool, float, str]]:
        """
        Verify a test audio sample against an enrolled voiceprint.

        Args:
            enrolled_voiceprint: 192-dim reference vector from enrollment.
            test_audio: Test recording path or 16 kHz waveform array.
            threshold: Optional override for cosine similarity decision threshold.

        Returns:
            Dict containing:
                - verified: True if genuine, False if impostor
                - decision: 'GENUINE' or 'IMPOSTOR'
                - cosine_similarity: similarity score in [-1.0, 1.0]
                - threshold: threshold applied
                - inference_time_ms: extraction latency in ms
        """
        thr = self.threshold if threshold is None else threshold
        test_emb, inf_time_s = self.extractor.extract_embedding(test_audio)
        similarity = compute_cosine_similarity(enrolled_voiceprint, test_emb)
        verified = bool(similarity >= thr)
        decision = "GENUINE" if verified else "IMPOSTOR"

        return {
            "verified": verified,
            "decision": decision,
            "cosine_similarity": float(similarity),
            "threshold": float(thr),
            "inference_time_ms": float(inf_time_s * 1000.0)
        }

    def identify(
        self,
        gallery: SpeakerGallery,
        test_audio: Union[str, Path, np.ndarray, torch.Tensor],
        threshold: Optional[float] = None,
        margin: Optional[float] = None,
        top_k: int = 5
    ) -> Dict[str, Union[bool, float, str, None, List]]:
        """
        Perform 1:N OPEN-SET speaker identification against a gallery of enrolments.

        Unlike verify(), no identity is claimed up front. The probe utterance is
        scored against every enrolled voiceprint, and the system answers
        "which enrolled user is this, if any?".

        Open-set rejection applies two independent conditions; BOTH must hold
        for an identification to be accepted:

          1. Absolute match:  top-1 cosine similarity >= threshold.
             Rejects probes from speakers who are not enrolled at all, which a
             pure nearest-neighbour search would otherwise map to whichever
             enrolled identity happens to be closest.

          2. Ranking margin:  (top-1 score - top-2 score) >= margin.
             Rejects probes that sit near-equidistant between two enrolments.
             Without this a probe scoring 0.41 vs 0.40 against two different
             users would be confidently identified as the first.

        A single-speaker gallery has no top-2 score, so condition 2 is
        vacuously satisfied and the decision reduces to condition 1.

        Args:
            gallery: SpeakerGallery holding the enrolled identities to search.
            test_audio: Probe recording path or 16 kHz waveform array.
            threshold: Optional override for the open-set acceptance threshold.
            margin: Optional override for the required top-1/top-2 separation.
            top_k: Number of ranked candidates to REPORT. This affects reporting
                   only: the top-2 score needed by the margin test is always
                   retrieved regardless of top_k, so top_k=1 cannot weaken the
                   accept/reject decision.

        Returns:
            Dict containing:
                - identified: True if an enrolled identity was accepted
                - identified_speaker_id: matched speaker_id, or None
                - decision: 'IDENTIFIED', 'REJECTED_NO_MATCH',
                            'REJECTED_AMBIGUOUS' or 'REJECTED_EMPTY_GALLERY'
                - rejection_reason: None, or the condition that failed
                - top1_score / top2_score / score_margin
                - candidates: ranked top-k list from the gallery search
                - gallery_size, threshold, margin, inference_time_ms
        """
        thr = self.identification_threshold if threshold is None else threshold
        mrg = self.identification_margin if margin is None else margin

        test_emb, inf_time_s = self.extractor.extract_embedding(test_audio)

        # The margin test needs the top-2 score, so always retrieve at least two
        # candidates for the DECISION even when the caller only wants one
        # REPORTED. Otherwise top_k=1 would silently disable the ambiguity check.
        search_k = None if top_k is None else max(int(top_k), 2)
        scored = gallery.search(test_emb, top_k=search_k)
        candidates = scored if top_k is None else scored[:top_k]

        base_result = {
            "gallery_size": len(gallery),
            "threshold": float(thr),
            "margin": float(mrg),
            "candidates": candidates,
            "inference_time_ms": float(inf_time_s * 1000.0),
        }

        if not scored:
            return {
                **base_result,
                "identified": False,
                "identified_speaker_id": None,
                "decision": "REJECTED_EMPTY_GALLERY",
                "rejection_reason": "NO_ENROLLED_IDENTITIES",
                "top1_score": None,
                "top2_score": None,
                "score_margin": None,
            }

        top1_score = float(scored[0]["cosine_similarity"])
        top2_score = float(scored[1]["cosine_similarity"]) if len(scored) > 1 else None
        score_margin = None if top2_score is None else top1_score - top2_score

        if top1_score < thr:
            decision = "REJECTED_NO_MATCH"
            rejection_reason = "NO_ENROLLED_SPEAKER_ABOVE_THRESHOLD"
            identified_speaker_id = None
        elif score_margin is not None and score_margin < mrg:
            decision = "REJECTED_AMBIGUOUS"
            rejection_reason = "INSUFFICIENT_MARGIN_BETWEEN_TOP_CANDIDATES"
            identified_speaker_id = None
        else:
            decision = "IDENTIFIED"
            rejection_reason = None
            identified_speaker_id = str(scored[0]["speaker_id"])

        return {
            **base_result,
            "identified": identified_speaker_id is not None,
            "identified_speaker_id": identified_speaker_id,
            "decision": decision,
            "rejection_reason": rejection_reason,
            "top1_score": top1_score,
            "top2_score": top2_score,
            "score_margin": score_margin,
        }
