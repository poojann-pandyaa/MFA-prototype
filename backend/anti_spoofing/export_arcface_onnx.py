"""
One-time conversion: DeepFace's ArcFace (TensorFlow/Keras) -> ONNX.

Same weights the project already ships with and downloads via DeepFace on
first run - this changes how they're served (onnxruntime instead of
DeepFace+TensorFlow at request time), not the model itself. TensorFlow,
DeepFace, and tf2onnx are needed only to run this script; the server
never imports them (see backend/requirements.txt vs requirements-dev.txt).

Usage:
    cd backend
    pip install -r requirements-dev.txt
    pip install --no-deps tf2onnx   # see requirements-dev.txt for why --no-deps
    python anti_spoofing/export_arcface_onnx.py
    # writes backend/models/arcface/arcface.onnx
"""
import os

# DeepFace builds its models against legacy Keras 2 (`tf_keras`), and
# TensorFlow decides which Keras `tf.keras` points at while *it* is being
# imported - so this has to be set before the tensorflow import below, not
# by DeepFace's own import further down. Without it tf.keras resolves to
# Keras 3, whose models tf2onnx cannot trace.
os.environ["TF_USE_LEGACY_KERAS"] = "1"
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

# Import order matters: onnx (pulled in by tf2onnx) and TensorFlow each
# bundle their own abseil/protobuf runtime, and importing onnx first
# deadlocks the process on macOS arm64. TensorFlow goes first.
import numpy as np
import tensorflow as tf
import tf2onnx
from deepface import DeepFace

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Its own subdirectory, deliberately not models/ alongside the PAD weights:
# services/pad_onnx.py's ensemble-fallback branch scans models/ for *every*
# .onnx file and parses crop scales out of their filenames, which an
# arcface.onnx sitting there would break.
OUT_PATH = os.path.join(CURRENT_DIR, "..", "models", "arcface", "arcface.onnx")

INPUT_SIZE = 112

# The exported graph should be arithmetically identical to the Keras one, so
# this is a "something is wrong" tolerance, not an accuracy budget - a clean
# conversion lands around 1e-6.
MAX_ABS_DIFF_TOLERANCE = 1e-4


def verify_export(keras_model) -> None:
    """Push one tensor through both graphs and compare. This is the only
    moment drift can be introduced - a tf2onnx or opset change, a different
    input signature - and it is silent at every later stage, so the export
    refuses to pass off a model it can't reproduce.

    Imported here rather than at module scope to keep TensorFlow first in the
    import order (see above)."""
    import onnxruntime as ort

    probe = np.random.RandomState(0).rand(1, INPUT_SIZE, INPUT_SIZE, 3).astype(np.float32)
    keras_output = keras_model.predict(probe, verbose=0)[0]

    session = ort.InferenceSession(OUT_PATH, providers=["CPUExecutionProvider"])
    onnx_output = session.run(None, {session.get_inputs()[0].name: probe})[0][0]

    max_abs_diff = float(np.abs(keras_output - onnx_output).max())
    cosine = float(
        np.dot(keras_output, onnx_output)
        / (np.linalg.norm(keras_output) * np.linalg.norm(onnx_output))
    )
    print(f"Self-check vs Keras: max abs diff {max_abs_diff:.3e}, cosine {cosine:.6f}")

    if max_abs_diff > MAX_ABS_DIFF_TOLERANCE:
        raise SystemExit(
            f"Export self-check FAILED: the ONNX graph disagrees with Keras by "
            f"{max_abs_diff:.3e} (tolerance {MAX_ABS_DIFF_TOLERANCE:.0e}). Refusing to "
            f"ship {OUT_PATH} - check the tf2onnx version, the opset, and the input "
            f"signature before using it."
        )


def main():
    model_wrapper = DeepFace.build_model("ArcFace")
    keras_model = model_wrapper.model

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    input_signature = [tf.TensorSpec([1, INPUT_SIZE, INPUT_SIZE, 3], tf.float32, name="input")]
    tf2onnx.convert.from_keras(
        keras_model, input_signature=input_signature, opset=13, output_path=OUT_PATH,
    )
    verify_export(keras_model)
    print(f"Exported ArcFace -> {OUT_PATH}")


if __name__ == "__main__":
    main()
