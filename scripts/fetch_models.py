from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AMFA_ENV", "development")

from amfa.common import artifacts

LLM = ("llm/qwen2.5-3b-instruct-q4_k_m.gguf", artifacts.LLM_SOURCE,
       artifacts.LLM_PINS["qwen2.5-3b-instruct-q4_k_m.gguf"])


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"user-agent": "amfa-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def place(dest: Path, data: bytes, expected: str) -> None:
    got = hashlib.sha256(data).hexdigest()
    if got != expected:
        sys.exit(f"REFUSED {dest.name}: sha256 {got[:16]}... does not match the pinned {expected[:16]}...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    models = Path(os.getenv("AMFA_MODELS_DIR", ROOT / "models"))
    bad = 0

    for pin in artifacts.PINS:
        dest = models / pin.path
        if dest.exists() and sha(dest) == pin.sha256:
            print(f"ok       {pin.path}")
            continue
        if a.verify or not pin.source.startswith("https://"):
            print(f"MISSING  {pin.path}  ({pin.source})" if not dest.exists() else f"MISMATCH {pin.path}")
            bad += 1
            continue
        print(f"fetching {pin.path}")
        data = download(pin.source)
        if pin.source.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                data = z.read(Path(pin.path).name)
        place(dest, data, pin.sha256)

    ecapa = models / artifacts.ECAPA_DIR
    for name, digest in artifacts.ECAPA_PINS.items():
        dest = ecapa / name
        if dest.exists() and sha(dest) == digest:
            print(f"ok       {artifacts.ECAPA_DIR}/{name}")
            continue
        if a.verify:
            print(f"MISSING  {artifacts.ECAPA_DIR}/{name}")
            bad += 1
            continue
        print(f"fetching {artifacts.ECAPA_DIR}/{name}")
        place(dest, download(artifacts.ECAPA_FILES_URL + name), digest)

    asr = models / artifacts.ASR_DIR
    asr_ok = all((asr / n).exists() and sha(asr / n) == d for n, d in artifacts.ASR_PINS.items())
    if asr_ok:
        print(f"ok       {artifacts.ASR_DIR}/ ({len(artifacts.ASR_PINS)} files)")
    elif a.verify:
        print(f"MISSING  {artifacts.ASR_DIR}/")
        bad += 1
    else:
        print(f"fetching {artifacts.ASR_DIR}/")
        data = download(artifacts.ASR_ZIP_URL)
        if hashlib.sha256(data).hexdigest() != artifacts.ASR_ZIP_SHA256:
            sys.exit("REFUSED speech-recognition model archive: sha256 mismatch")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name, digest in artifacts.ASR_PINS.items():
                member = f"{Path(artifacts.ASR_DIR).name}/{name}"
                place(asr / name, z.read(member), digest)

    if not a.no_llm:
        rel, url, digest = LLM
        dest = models / rel
        if dest.exists() and (not digest or sha(dest) == digest):
            print(f"ok       {rel}")
        elif a.verify:
            print(f"MISSING  {rel}")
            bad += 1
        else:
            print(f"fetching {rel} (about 2 GB)")
            data = download(url)
            if digest:
                place(dest, data, digest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                print(f"         sha256 {hashlib.sha256(data).hexdigest()} -- pin it with AMFA_LLM_SHA256")

    ueba = models / "ueba"
    if not (ueba / "isoforest.joblib").exists():
        print("note     UEBA artifacts absent: run  python -m amfa.factors.ueba.training.train")
    print("all artifacts verified" if not bad else f"{bad} artifact(s) missing or mismatched")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
