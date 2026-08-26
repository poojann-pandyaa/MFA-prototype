import base64
import json
import numpy as np
import cv2
from deepface import DeepFace
import os
import sys

# Setup for Silent-Face-Anti-Spoofing
current_dir = os.path.dirname(os.path.abspath(__file__))
anti_spoofing_path = os.path.join(current_dir, "..", "anti_spoofing")
if anti_spoofing_path not in sys.path:
    sys.path.append(anti_spoofing_path)

from src.anti_spoof_predict import AntiSpoofPredict
from src.generate_patches import CropImage
from src.utility import parse_model_name

# Initialize the anti-spoofing predictor
try:
    model_test = AntiSpoofPredict(device_id=0) 
    image_cropper = CropImage()
except Exception as e:
    print(f"Warning: Failed to init AntiSpoofPredict: {e}")
    model_test = None
    image_cropper = None

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
        
        # Threshold for ArcFace cosine is typically around 0.68.
        # We lower it significantly to 0.45 for strict security to prevent impostor access.
        threshold = 0.45
        if cosine_distance < threshold:
            return True
            
    except Exception as e:
        print(f"Verification failed: {e}")
        
    return False

def check_liveness(face_image_b64: str) -> bool:
    """Runs the Silent-Face-Anti-Spoofing model to determine if the face is live."""
    if not model_test:
        print("Anti-spoofing model not initialized properly.")
        return False

    img = b64_to_cv2(face_image_b64)
    
    image_bbox = model_test.get_bbox(img)
    if image_bbox is None:
        return False
    
    model_dir = os.path.join(anti_spoofing_path, "resources", "anti_spoof_models")
    prediction = np.zeros((1, 3))
    
    for model_name in os.listdir(model_dir):
        if not model_name.endswith('.pth'):
            continue
            
        h_input, w_input, model_type, scale = parse_model_name(model_name)
        param = {
            "org_img": img,
            "bbox": image_bbox,
            "scale": scale,
            "out_w": w_input,
            "out_h": h_input,
            "crop": True,
        }
        
        if scale is None:
            param["crop"] = False
            
        img_crop = image_cropper.crop(**param)
        prediction += model_test.predict(img_crop, os.path.join(model_dir, model_name))
        
    label = np.argmax(prediction)
    value = prediction[0][label] / 2
    
    print(f"Liveness label: {label}, confidence: {value}")
    
    # 1 corresponds to real face
    if label == 1 and value > 0.8:
        return True
        
    return False
