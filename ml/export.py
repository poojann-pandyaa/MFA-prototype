"""
Export a trained checkpoint to ONNX, with a numerical parity check before
writing the final file (same pattern as
backend/anti_spoofing/export_onnx.py).

    python export.py --checkpoint checkpoints/best.pt --out ../backend/models/pad_custom.onnx

The exported graph outputs a single sigmoid probability (see
PadModelInferenceOnly in model.py) - the attack-type and FFT heads are
training-time-only signals and aren't needed by the serving path.
"""
import argparse
import os

import numpy as np
import onnxruntime as ort
import torch

from data.manifest import INPUT_SIZE
from model import PadModel, PadModelInferenceOnly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--opset", type=int, default=18)
    args = ap.parse_args()

    model = PadModel(pretrained=False)
    model.load_state_dict(torch.load(args.checkpoint, map_location="cpu"))
    model.eval()

    export_model = PadModelInferenceOnly(model)
    export_model.eval()

    dummy = torch.rand(1, 3, INPUT_SIZE, INPUT_SIZE, dtype=torch.float32)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    torch.onnx.export(
        export_model,
        dummy,
        args.out,
        input_names=["input"],
        output_names=["live_probability"],
        opset_version=args.opset,
        dynamic_axes={"input": {0: "batch"}, "live_probability": {0: "batch"}},
    )

    # Parity check against a few random batches before trusting the file.
    sess = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
    max_diff = 0.0
    with torch.no_grad():
        for _ in range(5):
            batch = torch.rand(4, 3, INPUT_SIZE, INPUT_SIZE, dtype=torch.float32)
            torch_out = export_model(batch).numpy()
            onnx_out = sess.run(None, {"input": batch.numpy()})[0]
            max_diff = max(max_diff, float(np.abs(torch_out - onnx_out).max()))

    print(f"Exported to {args.out}")
    print(f"Parity check max abs diff over 5 random batches: {max_diff:.8f}")
    if max_diff > 1e-3:
        raise SystemExit("Parity check FAILED (diff too large) - do not ship this file.")
    print("Parity check PASSED.")


if __name__ == "__main__":
    main()
