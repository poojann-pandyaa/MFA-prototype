import base64
import json
import numpy as np
import cv2

import config
from services import pad_onnx

# DeepFace pulls in TensorFlow, which is slow to import and heavy in RAM.
# It's only needed for enrollment/face-matching, not for the liveness
# check, so it's imported lazily inside the functions that actually need
# it rather than at module load time.

def b64_to_cv2(b64_str: str):
    """Converts a base64 image string (with or without data:image... prefix) to a CV2 BGR image."""
    if "," in b64_str:
        b64_str = b64_str.split(",")[1]
    img_data = base64.b64decode(b64_str)
    nparr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is not None:
        h, w = img.shape[:2]
        max_dim = max(h, w)
        if max_dim > 800:
            scale = 800.0 / max_dim
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            
    return img

def extract_embedding(face_image_b64: str) -> str:
    """Extracts DeepFace embedding (ArcFace) from the base64 image."""
    from deepface import DeepFace
    img = b64_to_cv2(face_image_b64)
    try:
        embedding_objs = DeepFace.represent(img_path=img, model_name="ArcFace", enforce_detection=True)
        if len(embedding_objs) > 0:
            embedding = embedding_objs[0]["embedding"]
            return json.dumps(embedding)
    except Exception as e:
        print(f"Face extraction failed: {e}")
    return ""

def verify_face(face_image_b64: str, stored_embedding_str: str) -> bool:
    """Verifies a live face image against the stored embedding."""
    from deepface import DeepFace
    if not stored_embedding_str:
        return False

    img = b64_to_cv2(face_image_b64)
    stored_embedding = json.loads(stored_embedding_str)

    try:
        new_embedding_objs = DeepFace.represent(img_path=img, model_name="ArcFace", enforce_detection=True)
        if len(new_embedding_objs) == 0:
            return False

        new_embedding = new_embedding_objs[0]["embedding"]

        # Calculate cosine distance
        a = np.array(stored_embedding)
        b = np.array(new_embedding)
        cosine_distance = 1 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

        # ArcFace's own calibrated cosine threshold is ~0.68; this value is
        # config-driven (see backend/config.py) rather than hardcoded so it
        # can be recalibrated against real data via ml/eval.py.
        if cosine_distance < config.FACE_MATCH_THRESHOLD:
            return True

    except Exception as e:
        print(f"Verification failed: {e}")

    return False

def check_liveness(face_image_b64: str) -> bool:
    """Runs the anti-spoofing model (ONNX Runtime) to determine if the face is live."""
    img = b64_to_cv2(face_image_b64)
    if img is None:
        return False
    return pad_onnx.check_liveness(img, config.LIVENESS_THRESHOLD)
