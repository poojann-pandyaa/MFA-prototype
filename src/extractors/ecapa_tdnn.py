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
        threshold: float = 0.25
    ):
        """
        Initialize SpeakerVerifier.

        Args:
            extractor: Pre-initialized ECAPATDNNExtractor instance. If None, initialized automatically.
            threshold: Cosine similarity decision threshold (default: 0.25,
                       chosen based on Phase 1 benchmark separation gap:
                       highest impostor = 0.2234, lowest genuine = 0.3105).
        """
        self.extractor = extractor or ECAPATDNNExtractor()
        self.threshold = threshold

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
