from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from amfa.common import artifacts

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

PAD_THRESHOLD = float(os.getenv("AMFA_VOICE_PAD_THRESHOLD", "-0.6931471805599453"))
MATCH_THRESHOLD = max(float(os.getenv("AMFA_VOICE_MATCH_THRESHOLD", "0.25")), 0.20)
IDENT_THRESHOLD = max(float(os.getenv("AMFA_VOICE_IDENT_THRESHOLD", "0.30")), MATCH_THRESHOLD)
IDENT_MARGIN = max(float(os.getenv("AMFA_VOICE_IDENT_MARGIN", "0.05")), 0.0)
NB_SAMP = 64_600


def _torch():
    import torch
    torch.set_num_threads(int(os.getenv("AMFA_TORCH_THREADS", "2")))
    return torch


def pad_waveform(x: np.ndarray, max_len: int = NB_SAMP) -> np.ndarray:
    if len(x) >= max_len:
        return x[:max_len]
    return np.tile(x, int(max_len / len(x)) + 1)[:max_len]


class RawNet2PAD:
    def __init__(self) -> None:
        torch = _torch()
        from amfa.factors.voice.rawnet2_model import RawNet

        cfg = yaml.safe_load((Path(__file__).parent / "model_config_RawNet.yaml").read_text())
        ckpt = artifacts.verified("voice/pre_trained_DF_RawNet2.pth")
        self.model = RawNet(cfg["model"], "cpu")
        state = torch.load(str(ckpt), map_location="cpu", weights_only=True)
        self.model.load_state_dict(state, strict=True)
        self.model.eval()
        self._torch = torch
        self._lock = threading.Lock()
        self.version = "rawnet2-asvspoof2021-DF"

    def score(self, wav: np.ndarray) -> float:
        x = self._torch.from_numpy(pad_waveform(wav.astype(np.float32))).unsqueeze(0)
        with self._lock, self._torch.inference_mode():
            out = self.model(x)
        return float(out[0, 1].item())


class EcapaSpeaker:
    def __init__(self) -> None:
        torch = _torch()
        from speechbrain.inference.speaker import EncoderClassifier

        local = artifacts.verify_directory(artifacts.ECAPA_DIR, artifacts.ECAPA_PINS)
        # hyperparams.yaml names the hub repo as pretrained_path; point it at the verified local copy
        self.clf = EncoderClassifier.from_hparams(source=str(local), savedir=str(local),
                                                  overrides={"pretrained_path": str(local)},
                                                  run_opts={"device": "cpu"})
        self.clf.eval()
        self._torch = torch
        self._lock = threading.Lock()
        self.version = "ecapa-tdnn-voxceleb"

    def embed(self, wav: np.ndarray) -> np.ndarray:
        x = self._torch.from_numpy(wav.astype(np.float32)).unsqueeze(0)
        with self._lock, self._torch.inference_mode():
            e = self.clf.encode_batch(x).squeeze().cpu().numpy().astype(np.float32)
        n = float(np.linalg.norm(e))
        return e / n if n > 0 else e


@lru_cache(maxsize=1)
def pad() -> RawNet2PAD:
    return RawNet2PAD()


@lru_cache(maxsize=1)
def speaker() -> EcapaSpeaker:
    return EcapaSpeaker()


PROMPT_CHECK = (os.getenv("AMFA_VOICE_PROMPT_CHECK", "advisory") or "advisory").lower()
PROMPT_MIN_RECALL = float(os.getenv("AMFA_VOICE_PROMPT_MIN_RECALL", "0.6"))
DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def prompt_tokens(prompt: str) -> list[str]:
    out = []
    for t in prompt.lower().split():
        out.append(DIGIT_WORDS[int(t)] if t.isdigit() and len(t) == 1 else t)
    return out


def _lcs(a: list[str], b: list[str]) -> int:
    dp = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            cur = dp[j]
            dp[j] = prev + 1 if x == y else max(dp[j], dp[j - 1])
            prev = cur
    return dp[-1]


class PromptASR:

    def __init__(self) -> None:
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        path = artifacts.verify_directory(artifacts.ASR_DIR, artifacts.ASR_PINS)
        self.model = Model(str(path))
        self.version = "vosk-small-en-us-0.15"

    def check(self, wav: np.ndarray, prompt: str) -> dict:
        import json
        from vosk import KaldiRecognizer
        want = prompt_tokens(prompt)
        vocab = sorted(set(want) | set(DIGIT_WORDS))
        rec = KaldiRecognizer(self.model, 16000, json.dumps(vocab + ["[unk]"]))
        pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16).tobytes()
        rec.AcceptWaveform(pcm)
        heard = [w for w in json.loads(rec.FinalResult()).get("text", "").split() if w != "[unk]"]
        recall = _lcs(want, heard) / max(len(want), 1)
        return {"spoken": recall >= PROMPT_MIN_RECALL, "recall": round(recall, 3),
                "heardTokens": len(heard), "model": self.version}


@lru_cache(maxsize=1)
def asr() -> PromptASR:
    return PromptASR()
