from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from amfa import config

log = logging.getLogger("amfa.artifacts")


@dataclass(frozen=True)
class Pin:
    path: str
    sha256: str
    source: str
    license: str
    purpose: str


PINS: tuple[Pin, ...] = (
    Pin("face/2.7_80x80_MiniFASNetV2.onnx",
        "e6da1f2c06baebc489dc4bef97343d6de03c27abb577c6ca94b0a54e0287baf6",
        "MFA-prototype face-auth branch, export of minivision-ai/Silent-Face-Anti-Spoofing",
        "Apache-2.0", "face PAD, model 1 of 2"),
    Pin("face/2.7_80x80_MiniFASNetV2.onnx.data",
        "138ef1cf92cd95742936b65496ceba799257f0741406709c042efa0273790b5a",
        "as above", "Apache-2.0", "face PAD weights, model 1"),
    Pin("face/4_0_0_80x80_MiniFASNetV1SE.onnx",
        "e568c5e313818f82da7660d88a196b7b52b067cc71f64a7efd4531b69ea5cdf1",
        "as above", "Apache-2.0", "face PAD, model 2 of 2"),
    Pin("face/4_0_0_80x80_MiniFASNetV1SE.onnx.data",
        "c400781ff6aef1d476b2094ac2ac94b0323fa835a62dd63220d060b8cdce29bd",
        "as above", "Apache-2.0", "face PAD weights, model 2"),
    Pin("face/deploy.prototxt",
        "9fe2f141b4baee039ed9442da2833e216af40a6ff3e639e7b39258812bcda808",
        "Silent-Face-Anti-Spoofing resources/detection_model", "Apache-2.0",
        "RetinaFace detector graph used to crop for PAD"),
    Pin("face/Widerface-RetinaFace.caffemodel",
        "d08338a2c207df16a9c566f767fea67fb43ba6fff76ce11e938fe3fabefb9402",
        "Silent-Face-Anti-Spoofing resources/detection_model", "Apache-2.0",
        "RetinaFace detector weights used to crop for PAD"),
    Pin("face/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx", "MIT", "face detection + 5 landmarks"),
    Pin("face/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx", "Apache-2.0", "face embedding (128-d)"),
    Pin("voice/pre_trained_DF_RawNet2.pth",
        "52d8ad5f524a0f600c7c876d7a157a8f06c44a03504d0b2795c852f5e42c9127",
        "https://www.asvspoof.org/asvspoof2021/pre_trained_DF_RawNet2.zip",
        "ASVspoof 2021 baseline (research release)", "voice PAD"),
)

ECAPA_REPO = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_DIR = "voice/ecapa"
ECAPA_PINS: dict[str, str] = {
    "hyperparams.yaml": "6f78854fa04ba59e761437b76a2575d3aba5e5016de3e9b69f0c9a5077fb1a41",
    "embedding_model.ckpt": "0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2",
    "mean_var_norm_emb.ckpt": "cd70225b05b37be64fc5a95e24395d804231d43f74b2e1e5a513db7b69b34c33",
    "classifier.ckpt": "fd9e3634fe68bd0a427c95e354c0c677374f62b3f434e45b78599950d860d535",
    "label_encoder.txt": "e13c3a167bb4112685670ee896d20e2b565af16b3a4ceeaa8689fa4d22adb8b9",
}
ASR_DIR = "voice/asr/vosk-model-small-en-us-0.15"
ASR_ZIP_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
ASR_ZIP_SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
ASR_PINS: dict[str, str] = {
    "README": "c0cf286e4f7783306c5f6469b37db69228fb16803b03cae661edb2d7bba64ebb",
    "am/final.mdl": "75370a0137f9daf8f469dedd7daa4513ae7a621f03240c6e512e2b50b656a7b6",
    "conf/mfcc.conf": "1e2228006d01d805ad1c267fee9f79709ca87ac51bd82b0e3f5c69ba543f0fc4",
    "conf/model.conf": "8f14cb1eeb07c762c371db648c6be688d347236155ca0f64fb13b6567a8ce81f",
    "graph/Gr.fst": "023c8b7e30704a9e37765c635c252e608a02f361235bf94abdcf2a5225d85b20",
    "graph/HCLr.fst": "5caafba3081e1646545ac6bff0dd7a318e53dcbdc86f237909ce1d2ac1293d34",
    "graph/disambig_tid.int": "9ad87cc166d0998f08f758f47a6223a120dfffcbee805c4849c3aa5e6bb3c0fc",
    "graph/phones/word_boundary.int": "da199d9c991e0e84681ddbb34627b915b26302d50a8fdaa23c51e2bc3a50b5c3",
    "ivector/final.dubm": "8c5d7dd69d2122313baaf19f61f35dd3fa18b70c62ac0687e311e1c46e6daca7",
    "ivector/final.ie": "3f37faf90c375b9e4740b569398b5829ed9cc07d19be6d441f72c3b71d7efcc6",
    "ivector/final.mat": "ddd83586dc5f928cda8738b922c85ffe38fc789cb5f9151a712ca12f37265382",
    "ivector/global_cmvn.stats": "33be09afcc80059847a275c3d043b51f1ab954c7c2438ddbbf4745e8ba144ff9",
    "ivector/online_cmvn.conf": "a2f3571754b64297cb7efb2e7ca3df61995c5a45fcbb97188f90613552bb2dfe",
    "ivector/splice.conf": "9f0c5f7c82d18eaf25d8bce470efa9f7741f88411fe428774bc0a9bb69a24756",
}
ECAPA_FILES_URL = "https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb/resolve/main/"


LLM_PINS: dict[str, str] = {
    "qwen2.5-3b-instruct-q4_k_m.gguf": "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d",
    "qwen2.5-1.5b-instruct-q4_k_m.gguf": "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
}
LLM_SOURCE = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"


class ArtifactError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_llm(path: Path) -> None:
    pin = LLM_PINS.get(path.name)
    if pin is None:
        if config.get().production:
            raise ArtifactError(f"{path.name} is not a pinned model; add its SHA-256 to LLM_PINS")
        log.warning("loading unpinned model %s (development only)", path.name)
        return
    if sha256_file(path) != pin:
        raise ArtifactError(f"integrity check FAILED for {path.name} -- refusing to load")


def resolve(rel: str) -> Path:
    return config.get().models_dir / rel


def verified(rel: str) -> Path:
    pin = next((p for p in PINS if p.path == rel), None)
    if pin is None:
        raise ArtifactError(f"{rel} is not a pinned artifact")
    path = resolve(rel)
    if not path.exists():
        raise ArtifactError(f"missing model artifact {path}; run scripts/fetch_models.py")
    digest = sha256_file(path)
    if digest != pin.sha256:
        raise ArtifactError(f"integrity check FAILED for {rel}: expected {pin.sha256[:12]}..., "
                            f"found {digest[:12]}... -- refusing to load")
    return path


def verify_directory(rel_dir: str, pins: dict[str, str]) -> Path:
    base = resolve(rel_dir)
    if not pins:
        if config.get().production:
            raise ArtifactError(f"{rel_dir} has no recorded pins; run scripts/fetch_models.py "
                                "--pin-ecapa before deploying")
        log.warning("%s loaded without pins (development only)", rel_dir)
        return base
    for name, digest in pins.items():
        p = base / name
        if not p.exists() or sha256_file(p) != digest:
            raise ArtifactError(f"integrity check FAILED for {rel_dir}/{name}")
    return base


def status() -> list[dict]:
    out = []
    for p in PINS:
        path = resolve(p.path)
        ok = path.exists() and sha256_file(path) == p.sha256
        out.append({"path": p.path, "present": path.exists(), "verified": ok,
                    "license": p.license, "purpose": p.purpose})
    return out
