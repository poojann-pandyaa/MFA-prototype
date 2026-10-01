# backend/services/biometrics.py
import base64
import json

import cv2
import numpy as np

import config
from services import face_detect, pad_onnx, liveness_challenge, face_match_onnx


class RetakeNeededError(Exception):
    """Too few frames had a single, clearly-detectable face. Safe to
    surface to the client as-is - it carries no information about the
    PAD/gesture/face-match checks, unlike every other failure in
    verify_liveness_and_identity (see design spec's "Error handling")."""


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
    """Extracts an ArcFace embedding (ONNX Runtime) from a single base64
    image - used by /enroll, which only ever gets one photo and has no
    liveness requirement of its own."""
    img = b64_to_cv2(face_image_b64)
    if img is None:
        return ""
    detection = face_detect.detect(img)
    if detection.bbox is None or detection.multi_face:
        return ""
    aligned = face_match_onnx.align(img, detection.landmarks_px)
    embedding = face_match_onnx.embed(aligned)
    return json.dumps(embedding.tolist())


def verify_liveness_and_identity(images_b64: list, challenge_type: str, stored_embedding: str) -> bool:
    """Runs the full step-2 perception pipeline across a capture burst:
    per-frame detection, sequence-level PAD, gesture verification against
    challenge_type, and face-match on every PAD-live frame. Returns
    True only if all three pass (with every live frame matching); raises
    RetakeNeededError if too few frames had a single detectable face."""
    frames = [b64_to_cv2(b64) for b64 in images_b64]
    frames = [f for f in frames if f is not None]
    detections = [face_detect.detect(f) for f in frames]

    # A frame with more than one face is a possible attack signal (someone
    # else in frame, or a photo held up in front of the attacker's own
    # face) rather than a benign capture problem - it fails the same
    # generic way PAD/gesture/face-match failures do, not the "please
    # retake" path (see design spec's "Multiple faces in frame").
    if any(d.multi_face for d in detections):
        return False

    valid = [(f, d) for f, d in zip(frames, detections) if d.bbox is not None]
    if len(valid) < config.LIVENESS_MIN_VALID_FRAMES:
        raise RetakeNeededError("Could not clearly see a single face across enough frames. Please retake.")

    valid_frames = [f for f, _ in valid]
    valid_detections = [d for _, d in valid]
    bboxes = [d.bbox for d in valid_detections]

    live_mask = pad_onnx.live_frame_mask(valid_frames, bboxes, config.LIVENESS_THRESHOLD)
    if not pad_onnx.mask_passes(live_mask, config.LIVENESS_MIN_LIVE_FRAME_FRACTION):
        return False

    landmark_sequence = [d.landmarks_px for d in valid_detections]
    if not liveness_challenge.verify_gesture(landmark_sequence, challenge_type):
        return False

    # Identity is checked on EVERY PAD-live frame, and every one must match
    # the stored embedding. Matching only one frame (e.g. the largest) lets
    # an attacker perform the gesture live with their own face and splice in
    # a frame or two of the victim's photo: the PAD live-fraction tolerance
    # absorbs those frames, and they'd become the face-match frame. Frames
    # PAD called non-live are never used for matching. Cost is one ArcFace
    # ONNX call per live frame (~50ms on CPU; bursts are capped at
    # LIVENESS_MAX_FRAMES). Largest-bbox frame first, so the most reliable
    # crop is checked first and a mismatch exits early.
    live_indices = [i for i, is_live in enumerate(live_mask) if is_live]
    live_indices.sort(
        key=lambda i: valid_detections[i].bbox[2] * valid_detections[i].bbox[3],
        reverse=True,
    )
    for i in live_indices:
        aligned = face_match_onnx.align(valid_frames[i], valid_detections[i].landmarks_px)
        embedding = face_match_onnx.embed(aligned)
        if not face_match_onnx.verify(embedding, stored_embedding):
            return False
    return bool(live_indices)
