# backend/services/biometrics.py
import base64
import json
import logging

import cv2
import numpy as np

import config
from services import face_detect, pad_onnx, liveness_challenge, face_match_onnx

# Server-side diagnostics for step 2 (which stage ended the attempt, and the
# measured values). Never put any of this in an HTTP response - clients only
# ever see the generic failure / retake messages. Handler set up in main.py.
step2_log = logging.getLogger("mfa.step2")


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
        step2_log.info("step2 result=multi_face challenge=%s multi_face_frames=%d/%d",
                       challenge_type, sum(d.multi_face for d in detections), len(detections))
        return False

    valid = [(f, d) for f, d in zip(frames, detections) if d.bbox is not None]
    if len(valid) < config.LIVENESS_MIN_VALID_FRAMES:
        step2_log.info("step2 result=too_few_valid_frames challenge=%s valid=%d/%d min=%d",
                       challenge_type, len(valid), len(images_b64), config.LIVENESS_MIN_VALID_FRAMES)
        raise RetakeNeededError("Could not clearly see a single face across enough frames. Please retake.")

    valid_frames = [f for f, _ in valid]
    valid_detections = [d for _, d in valid]
    bboxes = [d.bbox for d in valid_detections]

    live_mask = pad_onnx.live_frame_mask(valid_frames, bboxes, config.LIVENESS_THRESHOLD)
    if not pad_onnx.mask_passes(live_mask, config.LIVENESS_MIN_LIVE_FRAME_FRACTION):
        step2_log.info("step2 result=pad_failed challenge=%s live=%s/%d min_fraction=%.2f",
                       challenge_type, "n/a" if live_mask is None else sum(live_mask),
                       len(valid_frames), config.LIVENESS_MIN_LIVE_FRAME_FRACTION)
        return False

    # Gesture evidence comes ONLY from PAD-live frames, in burst order. If
    # PAD-rejected frames counted, a couple of spliced frames (absorbed by
    # the live-fraction tolerance) could carry the gesture - e.g. 2 frames
    # with a shifted nose at one end forge a head turn, 1 closed-eye frame
    # forges a blink - while the genuine live frames carry the identity.
    # With the default config there are always >= 5 live frames here
    # (>= 0.8 * LIVENESS_MIN_VALID_FRAMES); if a looser config leaves fewer
    # than verify_gesture's minimum, it fails closed (returns False).
    landmark_sequence = [
        valid_detections[i].landmarks_px
        for i in range(len(valid_detections))
        if live_mask[i]
    ]
    gesture_info = (f"challenge={challenge_type} live_frames={len(landmark_sequence)} "
                    f"{liveness_challenge.describe_gesture(landmark_sequence, challenge_type)}")
    if not liveness_challenge.verify_gesture(landmark_sequence, challenge_type):
        step2_log.info("step2 result=gesture_failed %s", gesture_info)
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
    distances = []
    for i in live_indices:
        aligned = face_match_onnx.align(valid_frames[i], valid_detections[i].landmarks_px)
        embedding = face_match_onnx.embed(aligned)
        matched = face_match_onnx.verify(embedding, stored_embedding)
        distances.append(_distance_for_log(embedding, stored_embedding))
        if not matched:
            step2_log.info("step2 result=face_mismatch %s frames_checked=%d/%d distance=%s "
                           "threshold=%.3f distances=%s", gesture_info, len(distances),
                           len(live_indices), distances[-1], config.FACE_MATCH_THRESHOLD, distances)
            return False
    numeric = [d for d in distances if d != "n/a"]
    step2_log.info("step2 result=%s %s frames_matched=%d worst_distance=%s threshold=%.3f",
                   "passed" if live_indices else "no_live_frames", gesture_info, len(distances),
                   max(numeric) if numeric else "n/a", config.FACE_MATCH_THRESHOLD)
    return bool(live_indices)


def _distance_for_log(embedding, stored_embedding):
    """Cosine distance rounded for the diagnostics log, or "n/a" - logging
    must never be able to change (or crash) the verification outcome."""
    try:
        with np.errstate(divide="ignore", invalid="ignore"):
            return round(face_match_onnx.cosine_distance(embedding, stored_embedding), 3)
    except Exception:
        return "n/a"
