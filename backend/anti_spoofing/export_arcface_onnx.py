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
    # writes backend/models/arcface.onnx
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
import tensorflow as tf
import tf2onnx
from deepface import DeepFace

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(CURRENT_DIR, "..", "models", "arcface.onnx")

INPUT_SIZE = 112


def main():
    model_wrapper = DeepFace.build_model("ArcFace")
    keras_model = model_wrapper.model

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    input_signature = [tf.TensorSpec([1, INPUT_SIZE, INPUT_SIZE, 3], tf.float32, name="input")]
    tf2onnx.convert.from_keras(
        keras_model, input_signature=input_signature, opset=13, output_path=OUT_PATH,
    )
    print(f"Exported ArcFace -> {OUT_PATH}")


if __name__ == "__main__":
    main()
