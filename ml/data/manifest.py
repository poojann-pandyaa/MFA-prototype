"""
Unified dataset manifest + PyTorch Dataset for the PAD model.

Every dataset adapter (CelebA-Spoof, LCC-FASD, NUAA, your own capture...)
converges to the same manifest.csv schema so train.py/eval.py never need
to know which source dataset a row came from:

    path,label,attack_type,split,device

    path        - image path, absolute or relative to the manifest file
    label       - 0 = spoof, 1 = live
    attack_type - one of ATTACK_TYPES below
    split       - "train" | "val" | "test"
    device      - free-text capture device/source tag (used for
                  cross-device evaluation slicing in eval.py; "unknown"
                  is fine if the source dataset doesn't record it)
"""
import csv
import os
from dataclasses import dataclass

import cv2
import numpy as np
from torch.utils.data import Dataset

# Index 0 is reserved for "live" so attack-type CE and the binary head
# agree on what index 0 means when label == 1.
ATTACK_TYPES = ["live", "print", "replay_screen", "cutout_mask"]
ATTACK_TYPE_TO_IDX = {name: i for i, name in enumerate(ATTACK_TYPES)}

INPUT_SIZE = 128
FFT_MAP_SIZE = 32


@dataclass
class ManifestRow:
    path: str
    label: int
    attack_type: str
    split: str
    device: str


def read_manifest(manifest_path: str, split: str = None) -> list[ManifestRow]:
    rows = []
    base_dir = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path, newline="") as f:
        for r in csv.DictReader(f):
            if split is not None and r["split"] != split:
                continue
            path = r["path"]
            if not os.path.isabs(path):
                path = os.path.join(base_dir, path)
            rows.append(ManifestRow(
                path=path,
                label=int(r["label"]),
                attack_type=r["attack_type"],
                split=r["split"],
                device=r.get("device", "unknown") or "unknown",
            ))
    return rows


def write_manifest(manifest_path: str, rows: list[ManifestRow]):
    with open(manifest_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "label", "attack_type", "split", "device"])
        for r in rows:
            writer.writerow([r.path, r.label, r.attack_type, r.split, r.device])


def _fft_log_magnitude(gray: np.ndarray, out_size: int = FFT_MAP_SIZE) -> np.ndarray:
    """
    Free auxiliary supervision target: log-magnitude of the 2D FFT,
    resized to a small fixed map. Print/replay attacks leave moire and
    grid artifacts here that a live face crop doesn't have - see ml/README.md.
    """
    f = np.fft.fft2(gray.astype(np.float32))
    f_shifted = np.fft.fftshift(f)
    magnitude = np.log1p(np.abs(f_shifted))
    magnitude = cv2.resize(magnitude, (out_size, out_size), interpolation=cv2.INTER_AREA)
    # Normalize to roughly [0, 1] for stable MSE loss magnitudes.
    max_val = magnitude.max()
    if max_val > 1e-6:
        magnitude = magnitude / max_val
    return magnitude.astype(np.float32)


class PadDataset(Dataset):
    """
    Loads a face crop, resizes to INPUT_SIZE, computes the FFT auxiliary
    target on the fly, and returns (image_chw, binary_label, attack_idx, fft_map).

    Assumes `path` already points to a roughly-cropped face (this is
    consistent with how the backend feeds crops to the PAD model - see
    backend/services/pad_onnx.py - rather than full uncropped photos).
    Adapters are responsible for cropping at prep time.
    """

    def __init__(self, rows: list[ManifestRow], augment: bool = False):
        self.rows = rows
        self.augment = augment

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, idx: int):
        row = self.rows[idx]
        img = cv2.imread(row.path, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"Could not read image: {row.path}")
        img = cv2.resize(img, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_AREA)

        if self.augment:
            img = _augment(img)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        fft_map = _fft_log_magnitude(gray)

        img_chw = np.transpose(img.astype(np.float32) / 255.0, (2, 0, 1))
        attack_idx = ATTACK_TYPE_TO_IDX.get(row.attack_type, ATTACK_TYPE_TO_IDX["print"])

        return img_chw, np.float32(row.label), np.int64(attack_idx), fft_map


def _augment(img: np.ndarray) -> np.ndarray:
    """Lightweight augmentation simulating device/lighting variation."""
    if np.random.rand() < 0.5:
        img = cv2.flip(img, 1)
    if np.random.rand() < 0.5:
        alpha = np.random.uniform(0.7, 1.3)  # contrast
        beta = np.random.uniform(-25, 25)    # brightness
        img = np.clip(img.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
    if np.random.rand() < 0.3:
        quality = np.random.randint(35, 90)
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    if np.random.rand() < 0.3:
        scale = np.random.uniform(0.5, 0.9)
        h, w = img.shape[:2]
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        img = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return img
