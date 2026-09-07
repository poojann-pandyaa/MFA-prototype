import torchaudio
import soundfile as sf
from pathlib import Path
from typing import Tuple
import torch

def get_audio_info(file_path: Path) -> dict:
    """Extract metadata such as duration in seconds, sample rate, and channels."""
    info = sf.info(str(file_path))
    return {
        "duration_sec": info.duration,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "frames": info.frames
    }

def load_audio(file_path: Path, target_sample_rate: int = 16000) -> Tuple[torch.Tensor, int]:
    """Load audio file as torch tensor and resample to target_sample_rate if needed."""
    waveform, sample_rate = torchaudio.load(str(file_path))
    
    # Convert stereo to mono if needed
    if waveform.shape[0] > 1:
        waveform = torch.mean(waveform, dim=0, keepdim=True)
        
    if sample_rate != target_sample_rate:
        resampler = torchaudio.transforms.Resample(orig_freq=sample_rate, new_freq=target_sample_rate)
        waveform = resampler(waveform)
        sample_rate = target_sample_rate
        
    return waveform, sample_rate
