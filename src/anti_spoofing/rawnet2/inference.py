"""
RawNet2 Inference Wrapper for Presentation Attack Detection (PAD).

Based on the NTU-ROSE RawNet2 model and ASVspoof 2021 evaluation pipeline.
Attributes:
  - Input: 16 kHz mono waveform, padded/sliced to 64,600 samples (~4.0375 seconds).
  - Model Output: LogSoftmax over 2 classes: [0: SPOOF, 1: BONAFIDE].
  - rawnet2_score: BONAFIDE log-probability (batch_out[:, 1]).
  - Predicted label: BONAFIDE if score > threshold, else SPOOF.
"""

import math
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

import numpy as np
import soundfile as sf
import torch
import yaml

from .model import RawNet


def pad_waveform(x: np.ndarray, max_len: int = 64600) -> np.ndarray:
    """
    Pad or crop waveform according to upstream NTU-ROSE RawNet2 data_utils.py.
    
    If waveform is shorter than max_len, tile/repeat it until reaching max_len.
    If longer or equal, slice first max_len samples.
    """
    x_len = x.shape[0]
    if x_len >= max_len:
        return x[:max_len]
    num_repeats = int(max_len / x_len) + 1
    padded_x = np.tile(x, num_repeats)[:max_len]
    return padded_x


PAD_PEAK = 0.9       # the level the model is given; ASVspoof's training audio is loud and clean
PAD_TRIM_DB = 30.0   # frames this far below the loudest are silence at the ends of a recording


def prepare_for_pad(x: np.ndarray, sr: int = 16000) -> np.ndarray:
    """
    Cut the silence at both ends of a recording and bring it to a fixed peak level.

    Measured 2026-11-18 with the ASVspoof 2021 DF checkpoint on real recorded speech: the model
    scored it about 0 (BONAFIDE) as recorded but about -12 at a tenth of the volume, the same as
    silence (-12.1) and the same as every phone recording it had heard (-11.5 to -11.98). Leading
    silence also pulled some real samples down to -8.5 or -11.1. With this step 5 of 7 real samples
    scored BONAFIDE whatever their volume, and white noise, which it had called BONAFIDE, scored SPOOF.
    """
    w = np.asarray(x, dtype=np.float32)
    frame = sr // 40                                   # 25 ms
    n = len(w) // frame
    if n:
        rms = np.sqrt((w[: n * frame].reshape(n, frame) ** 2).mean(axis=1) + 1e-12)
        loud = np.flatnonzero(rms >= rms.max() * 10 ** (-PAD_TRIM_DB / 20))
        if len(loud):
            margin = sr // 10                          # keep 100 ms either side
            w = w[max(0, loud[0] * frame - margin): min(len(w), (loud[-1] + 1) * frame + margin)]
    peak = float(np.abs(w).max()) if len(w) else 0.0
    return w if peak < 1e-4 else (w * (PAD_PEAK / peak)).astype(np.float32)


class RawNet2Detector:
    """
    Inference detector wrapping NTU-ROSE RawNet2 model.
    """
    def __init__(
        self,
        checkpoint_path: Union[str, Path],
        config_path: Optional[Union[str, Path]] = None,
        device: Optional[str] = None,
        decision_threshold: float = -0.69314718,  # log(0.5) by default
        level_input: bool = True
    ):
        """
        Initialize RawNet2 detector.

        Args:
            checkpoint_path: Path to pretrained .pth checkpoint file.
            config_path: Optional path to model_config_RawNet.yaml. Defaults to bundled yaml.
            device: 'cuda', 'cpu', or None (auto-select).
            decision_threshold: BONAFIDE log-probability threshold. Default is log(0.5) ≈ -0.6931.
            level_input: cut silence at the ends and normalise the level first (prepare_for_pad).
                The results in results/rawnet2/ were produced without it (False reproduces them).
        """
        self.level_input = level_input
        self.checkpoint_path = Path(checkpoint_path)
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found at: {self.checkpoint_path}")

        if config_path is None:
            config_path = Path(__file__).parent / "model_config_RawNet.yaml"
        self.config_path = Path(config_path)

        with open(self.config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.model_args = cfg["model"]
        self.max_len = self.model_args.get("nb_samp", 64600)
        self.decision_threshold = decision_threshold

        if device is None:
            self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        # Build and load model
        t0 = time.perf_counter()
        self.model = RawNet(self.model_args, str(self.device)).to(self.device)
        state_dict = torch.load(self.checkpoint_path, map_location=self.device)
        self.model.load_state_dict(state_dict, strict=True)
        self.model.eval()
        self.load_time_s = time.perf_counter() - t0

    def preprocess_audio(self, audio_input: Union[str, Path, np.ndarray]) -> torch.Tensor:
        """
        Load and preprocess audio into model input tensor.
        Ensures 16 kHz, single-channel (mono), tiled/sliced to 64,600 samples.
        """
        if isinstance(audio_input, (str, Path)):
            audio_path = Path(audio_input)
            data, sr = sf.read(str(audio_path), dtype="float32")
            if sr != 16000:
                raise ValueError(f"Expected 16,000 Hz sample rate, got {sr} Hz for {audio_path.name}")
            if data.ndim > 1:
                # Average to mono
                data = np.mean(data, axis=1)
        elif isinstance(audio_input, np.ndarray):
            data = audio_input.astype(np.float32)
            if data.ndim > 1:
                data = np.mean(data, axis=1)
        else:
            raise TypeError(f"Unsupported audio input type: {type(audio_input)}")

        if self.level_input:
            data = prepare_for_pad(data)
        # Upstream pad logic
        padded = pad_waveform(data, max_len=self.max_len)
        tensor = torch.from_numpy(padded).unsqueeze(0).to(self.device)  # (1, 64600)
        return tensor

    @torch.inference_mode()
    def predict(
        self,
        audio_input: Union[str, Path, np.ndarray],
        threshold: Optional[float] = None
    ) -> Dict[str, Union[float, str, Dict[str, float]]]:
        """
        Perform anti-spoofing prediction on single audio sample.

        Args:
            audio_input: File path (WAV/FLAC) or 1D numpy array at 16 kHz.
            threshold: Optional override for BONAFIDE log-probability threshold.

        Returns:
            Dict containing:
                - rawnet2_score: BONAFIDE log-probability (model output[:, 1])
                - predicted_label: 'BONAFIDE' or 'SPOOF'
                - prob_bonafide: exp(bonafide_log_prob)
                - prob_spoof: exp(spoof_log_prob)
                - inference_time_ms: inference latency in milliseconds
                - device: computation device string
        """
        thr = self.decision_threshold if threshold is None else threshold
        inp_tensor = self.preprocess_audio(audio_input)

        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        t0 = time.perf_counter()

        output = self.model(inp_tensor)  # (1, 2) LogSoftmax

        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        inference_time_ms = (time.perf_counter() - t0) * 1000.0

        # LogSoftmax output: [0: SPOOF, 1: BONAFIDE]
        log_p_spoof = float(output[0, 0].item())
        log_p_bonafide = float(output[0, 1].item())

        # BONAFIDE log-probability is the canonical score
        score = log_p_bonafide
        predicted_label = "BONAFIDE" if score > thr else "SPOOF"

        prob_bonafide = math.exp(log_p_bonafide)
        prob_spoof = math.exp(log_p_spoof)

        return {
            "rawnet2_score": score,
            "predicted_label": predicted_label,
            "prob_bonafide": prob_bonafide,
            "prob_spoof": prob_spoof,
            "log_p_spoof": log_p_spoof,
            "log_p_bonafide": log_p_bonafide,
            "inference_time_ms": inference_time_ms,
            "device": str(self.device)
        }
