"""
One-time conversion: vendored MiniFASNet .pth weights -> ONNX.

This is the *only* place in the project that needs `torch` at all - the
serving path (backend/services/pad_onnx.py) uses onnxruntime exclusively,
which is smaller, starts faster, and uses noticeably less RAM than keeping
a full PyTorch install in the request path.

No training happens here: these are the same pretrained weights the
project already shipped (backend/anti_spoofing/resources/anti_spoof_models),
just re-exported to a portable, fast-inference format.

Usage:
    cd backend/anti_spoofing
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    python export_onnx.py
    # writes backend/models/<name>.onnx
    # torch can be uninstalled afterwards; it is not a serving dependency.
"""
import os
import sys

import torch

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

from src.model_lib.MiniFASNet import MiniFASNetV1, MiniFASNetV2, MiniFASNetV1SE, MiniFASNetV2SE
from src.utility import get_kernel, parse_model_name

MODEL_MAPPING = {
    "MiniFASNetV1": MiniFASNetV1,
    "MiniFASNetV2": MiniFASNetV2,
    "MiniFASNetV1SE": MiniFASNetV1SE,
    "MiniFASNetV2SE": MiniFASNetV2SE,
}

SRC_DIR = os.path.join(CURRENT_DIR, "resources", "anti_spoof_models")
OUT_DIR = os.path.join(CURRENT_DIR, "..", "models")


def load_torch_model(model_path: str):
    model_name = os.path.basename(model_path)
    h_input, w_input, model_type, _ = parse_model_name(model_name)
    kernel_size = get_kernel(h_input, w_input)
    model = MODEL_MAPPING[model_type](conv6_kernel=kernel_size)

    state_dict = torch.load(model_path, map_location="cpu")
    keys = iter(state_dict)
    first_layer_name = next(keys)
    if first_layer_name.find("module.") >= 0:
        from collections import OrderedDict
        new_state_dict = OrderedDict()
        for key, value in state_dict.items():
            new_state_dict[key[7:]] = value
        model.load_state_dict(new_state_dict)
    else:
        model.load_state_dict(state_dict)

    model.eval()
    return model, h_input, w_input


def export_one(model_path: str):
    model_name = os.path.basename(model_path)
    model, h_input, w_input = load_torch_model(model_path)

    dummy = torch.randn(1, 3, h_input, w_input, dtype=torch.float32)
    out_path = os.path.join(OUT_DIR, model_name.replace(".pth", ".onnx"))
    os.makedirs(OUT_DIR, exist_ok=True)

    torch.onnx.export(
        model,
        dummy,
        out_path,
        input_names=["input"],
        output_names=["logits"],
        opset_version=18,
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
    )
    print(f"Exported {model_name} -> {out_path} (input {h_input}x{w_input})")


def main():
    pth_files = [f for f in os.listdir(SRC_DIR) if f.endswith(".pth")]
    if not pth_files:
        print(f"No .pth files found in {SRC_DIR}")
        return
    for f in pth_files:
        export_one(os.path.join(SRC_DIR, f))


if __name__ == "__main__":
    main()
