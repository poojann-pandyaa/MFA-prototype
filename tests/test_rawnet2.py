import numpy as np
import torch
import yaml
from pathlib import Path

from src.anti_spoofing.rawnet2.inference import pad_waveform
from src.anti_spoofing.rawnet2.model import RawNet


def test_pad_waveform_short():
    # Short waveform (< 64,600) should be repeated and sliced to 64,600
    short_audio = np.ones(16000, dtype=np.float32)
    padded = pad_waveform(short_audio, max_len=64600)
    assert len(padded) == 64600
    assert padded[0] == 1.0
    assert padded[-1] == 1.0


def test_pad_waveform_long():
    # Long waveform (> 64,600) should be truncated to first 64,600
    long_audio = np.arange(80000, dtype=np.float32)
    padded = pad_waveform(long_audio, max_len=64600)
    assert len(padded) == 64600
    assert padded[0] == 0.0
    assert padded[-1] == 64599.0


def test_rawnet2_model_forward():
    # Verify RawNet forward pass with synthetic waveform tensor
    config_path = Path(__file__).resolve().parent.parent / "src" / "anti_spoofing" / "rawnet2" / "model_config_RawNet.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    device = "cpu"
    model = RawNet(cfg["model"], device).to(device)
    model.eval()

    # Synthetic batch: 1 audio sample of 64,600 samples
    dummy_input = torch.randn(1, 64600, device=device)
    with torch.no_grad():
        out = model(dummy_input)

    assert out.shape == (1, 2)
    # Output is LogSoftmax: exp(out).sum() should equal 1.0
    probs = torch.exp(out).sum(dim=1)
    assert torch.isclose(probs, torch.tensor([1.0]), atol=1e-4)



# ---- prepare_for_pad: what the model is given ----------------------------------------------------

def _tone(seconds=2.0, level=0.5):
    t = np.arange(int(16000 * seconds)) / 16000
    return (level * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def test_a_quiet_recording_reaches_the_model_at_the_same_level_as_a_loud_one():
    from src.anti_spoofing.rawnet2.inference import PAD_PEAK, prepare_for_pad
    loud, quiet = prepare_for_pad(_tone(level=0.5)), prepare_for_pad(_tone(level=0.05))
    assert abs(float(np.abs(loud).max()) - PAD_PEAK) < 1e-4 and np.allclose(loud, quiet, atol=1e-3)


def test_silence_at_the_ends_is_cut_off():
    from src.anti_spoofing.rawnet2.inference import prepare_for_pad
    w = np.concatenate([np.zeros(24000, np.float32), _tone(), np.zeros(16000, np.float32)])
    assert len(prepare_for_pad(w)) < len(w) - 30000


def test_silence_is_left_as_it_is():
    from src.anti_spoofing.rawnet2.inference import prepare_for_pad
    z = np.zeros(32000, np.float32)
    assert np.array_equal(prepare_for_pad(z), z)
