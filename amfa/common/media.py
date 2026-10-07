from __future__ import annotations

import base64
import binascii
import hashlib
import io

import numpy as np

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 4096 * 4096
MAX_IMAGE_DIM = 1280

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MIN_AUDIO_S = 1.5
MAX_AUDIO_S = 15.0
TARGET_SR = 16_000


class MediaError(ValueError):

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _b64(data: str, limit: int) -> bytes:
    if not isinstance(data, str):
        raise MediaError("MEDIA_NOT_STRING")
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    if len(data) > limit * 4 // 3 + 16:
        raise MediaError("MEDIA_TOO_LARGE")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaError("MEDIA_NOT_BASE64") from exc
    if len(raw) > limit:
        raise MediaError("MEDIA_TOO_LARGE")
    return raw


def decode_image(data: str) -> tuple[np.ndarray, str]:
    import cv2

    raw = _b64(data, MAX_IMAGE_BYTES)
    if not (raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n"
            or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")):
        raise MediaError("IMAGE_TYPE_UNSUPPORTED")

    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        with Image.open(io.BytesIO(raw)) as probe:
            w, h = probe.size
    except Exception as exc:
        raise MediaError("IMAGE_UNREADABLE") from exc
    if w * h > MAX_IMAGE_PIXELS or w < 64 or h < 64:
        raise MediaError("IMAGE_DIMENSIONS_INVALID")

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise MediaError("IMAGE_UNREADABLE")
    ih, iw = img.shape[:2]
    scale = MAX_IMAGE_DIM / max(ih, iw)
    if scale < 1.0:
        img = cv2.resize(img, (int(iw * scale), int(ih * scale)), interpolation=cv2.INTER_AREA)
    return img, hashlib.sha256(raw).hexdigest()


def decode_audio(data: str) -> tuple[np.ndarray, str, dict]:
    import soundfile as sf

    raw = _b64(data, MAX_AUDIO_BYTES)
    if not ((raw[:4] == b"RIFF" and raw[8:12] == b"WAVE") or raw[:4] == b"fLaC"):
        raise MediaError("AUDIO_TYPE_UNSUPPORTED")
    try:
        info = sf.info(io.BytesIO(raw))
        if info.frames / max(info.samplerate, 1) > MAX_AUDIO_S + 1:
            raise MediaError("AUDIO_TOO_LONG")
        wav, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("AUDIO_UNREADABLE") from exc

    wav = wav.mean(axis=1)
    if sr != TARGET_SR:
        if not 8_000 <= sr <= 96_000:
            raise MediaError("AUDIO_SAMPLE_RATE_UNSUPPORTED")
        from scipy.signal import resample_poly
        g = np.gcd(int(sr), TARGET_SR)
        wav = resample_poly(wav, TARGET_SR // g, int(sr) // g).astype(np.float32)

    duration = len(wav) / TARGET_SR
    if duration < MIN_AUDIO_S:
        raise MediaError("AUDIO_TOO_SHORT")
    if duration > MAX_AUDIO_S:
        raise MediaError("AUDIO_TOO_LONG")
    if not np.all(np.isfinite(wav)):
        raise MediaError("AUDIO_UNREADABLE")

    quality = audio_quality(wav)
    return wav, hashlib.sha256(raw).hexdigest(), quality


def audio_quality(wav: np.ndarray) -> dict:
    frame = 400
    n = len(wav) // frame
    frames = wav[: n * frame].reshape(n, frame) if n else wav.reshape(1, -1)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    noise_floor = float(np.percentile(db, 10))
    voiced = db > max(noise_floor + 12.0, -50.0)
    return {
        "durationS": round(len(wav) / TARGET_SR, 2),
        "rmsDb": round(float(20 * np.log10(np.sqrt((wav ** 2).mean()) + 1e-12)), 1),
        "clippedRatio": round(float((np.abs(wav) > 0.99).mean()), 4),
        "voicedS": round(float(voiced.sum() * frame / TARGET_SR), 2),
        "snrDb": round(float(np.percentile(db, 90) - noise_floor), 1),
    }
