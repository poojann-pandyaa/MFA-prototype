# Active challenge-response liveness + unified face-detection serving path

Date: 2026-10-01
Status: Draft, awaiting review

## Context

The current step-2 flow accepts a single still frame from the browser webcam and runs it through two independent, security-relevant checks: a passive presentation-attack-detection (PAD) model (`backend/services/pad_onnx.py`), and ArcFace face-matching via the DeepFace library (`backend/services/biometrics.py`). The project's own README documents the resulting gap directly:

> "the client posts a base64 image to the server - there's no proof it came from a live camera versus a file upload."

A single still frame cannot close that gap no matter how good the PAD model is: a high-quality photo, a screen replay, or an injected frame all arrive at the server identically. This is unacceptable for a banking-adjacent MFA system, where the realistic threat model includes an attacker with a recording or photo of the legitimate user.

Separately, the current serving path runs **three different face detectors** across a single login attempt: BlazeFace client-side (TensorFlow.js, pure UX), a vendored RetinaFace-Caffe detector server-side inside `pad_onnx.py`, and DeepFace's own internal detector server-side before face-matching. DeepFace also pulls in TensorFlow at import time, which the project's own code comments already flag as a measured cost ("slow to import, heavy in RAM") — this is the single heaviest, slowest piece of the per-login server-side path today.

This spec covers both, because they touch the same request path and the same question of "what produces the face crop used for the security decision":

1. **Active challenge-response liveness** — replace the single-frame upload with a randomized action prompt (blink / turn head left / turn head right) and a short frame burst, verified server-side.
2. **Unified detection + ArcFace serving path** — replace the three separate detectors with one (MediaPipe Face Landmarker, server-side), and remove TensorFlow from the request path by serving ArcFace via ONNX Runtime, the same pattern already used for PAD.

## Goals

- Close the "no proof of live capture" gap with a mechanism that resists replay of a pre-recorded video or photo of the legitimate user, not just static-photo spoofing.
- Keep the web-only scope (responsive React app, works in mobile and desktop browsers) — no native app, no PWA-specific work.
- Reduce per-login server-side latency and memory by removing redundant face detection passes and the TensorFlow dependency from the request path.
- Keep every model that is already a good choice (ArcFace, the custom PAD model) exactly as-is — this is a serving-path and protocol change, not a model-quality change.
- Stay entirely on free, open-source, no-approval-required libraries and models throughout.

## Non-goals (explicitly out of scope for this spec)

- Training the custom PAD model on real data (`ml/` pipeline execution) — separate, already-identified work item.
- 3D-mask/silicone/makeup attack classes, deepfake/injection detection, domain-adversarial training, rPPG — all previously documented as known gaps in `ml/README.md`; unchanged by this spec.
- Native mobile app or installable PWA — user has confirmed web-only for now.
- Multi-face / tailgating rejection is included here as a small addition (see Error handling) since it falls out of the new per-frame detection loop almost for free, but is not the primary goal.

## Architecture overview

The existing `VerificationSession` binding (user + device + TTL + single-use, in `backend/services/auth.py`) is unchanged and continues to be the thing that stops step 2 from being callable as a standalone password bypass. What changes is what step 2 receives and how it's checked:

```
step 1 (password) success
  -> create_verification_session() also picks challenge_type at random
  -> response includes {session_id, challenge_type}

frontend prompts the action, captures ~13 frames over ~2s
  -> POST /login/step2 {session_id, images: [...]}

backend, per request:
  1. consume_verification_session()            [unchanged]
  2. for each frame: MediaPipe detect+landmark  [new, replaces 2 of the 3 detectors]
  3. drop frames with no face; require a minimum valid-frame count
  4. PAD check across the valid frames          [pad_onnx, extended to a sequence]
  5. gesture check across the valid frames       [new: liveness_challenge.py]
  6. face-match on the best single frame         [ONNX ArcFace, replaces DeepFace at request time]
  7. any failure in 3-6 -> same generic 401
```

Face match is now **downstream of, and gated by,** both the PAD and gesture checks — it no longer runs independently of liveness. This is also a quiet correctness fix for today's implicit ordering (currently each check is independent and any of them being skipped or misconfigured doesn't block the others).

## Components

### `backend/services/face_detect.py` (new)

Wraps MediaPipe Face Landmarker (Apache-2.0, CPU, no API key, no network calls at runtime). One call per frame returns a bounding box (converted to the existing `[left, top, width, height]` int convention so it's a drop-in for `CropImage.crop()`, already used by PAD) plus the full landmark set. This is the single detector used everywhere downstream — BlazeFace remains client-side only, as a pure UX hint, and is not touched by this spec since it is never part of the trust boundary.

### `backend/services/pad_onnx.py` (modified)

- Remove `_FaceDetector` (the vendored RetinaFace-Caffe detector) — detection now comes from `face_detect.py`, passed in rather than computed internally.
- Add `check_liveness_sequence(frames_with_bboxes, threshold) -> bool`: runs the existing per-frame `predict()` logic (unchanged model, unchanged preprocessing) across all valid frames and requires a minimum live-frame fraction (new config constant, suggested default 0.8) rather than a single pass/fail.
- The legacy MiniFASNet ensemble fallback path (used only when `pad_custom.onnx` is absent) keeps working off the same adapted bbox format; its calibration was never pixel-exact against any one detector and is not the path being tuned going forward, so no recalibration is attempted here.

### `backend/services/liveness_challenge.py` (new)

Takes the landmark sequence for the valid frames plus the session's `challenge_type` and returns pass/fail:

- `blink`: eye-aspect-ratio (EAR) computed per frame from the eye landmarks; pass requires EAR to dip below a configured threshold and recover within the burst window.
- `turn_left` / `turn_right`: nose-tip x-displacement relative to inter-eye distance (for scale invariance); pass requires consistent displacement in the requested direction exceeding a configured threshold.

Pure landmark-array-in, bool-out — no I/O, no model loading — so it's directly unit-testable with synthetic landmark sequences (crafted EAR curves, crafted displacement trajectories) without a camera or real video in CI.

### `backend/services/face_match_onnx.py` (new)

Replaces the request-time half of `biometrics.py`'s DeepFace usage:

- Loads `arcface.onnx` once at process start via `onnxruntime` (same loading pattern as `pad_onnx.py`).
- `align(frame, landmarks) -> aligned_112x112_crop`: derives the standard 5-point alignment (eye centers, nose tip, mouth corners) from the MediaPipe landmarks and applies the canonical ArcFace similarity-transform alignment. This step matters — ArcFace's accuracy depends on consistent alignment, and DeepFace was doing this alignment internally and invisibly; it must be replicated explicitly now that DeepFace is removed from the request path.
- `embed(aligned_crop) -> np.ndarray`: single ONNX Runtime forward pass.
- `verify(embedding, stored_embedding) -> bool`: unchanged cosine-distance logic against `config.FACE_MATCH_THRESHOLD`, moved here from `biometrics.py`.

### `backend/anti_spoofing/export_arcface_onnx.py` (new, one-time/dev-time script)

Analogous to the existing `export_onnx.py` for PAD: loads the ArcFace weights DeepFace already uses today, exports to `arcface.onnx`, runs a parity check against the DeepFace/TensorFlow output on a sample batch before writing the file. **Same weights the project already ships with** — this is a serving-path change, not a model or license change. TensorFlow and DeepFace become dev-time-only dependencies (needed once, to regenerate `arcface.onnx`, same way PyTorch is already dev-time-only for regenerating PAD's ONNX files), not runtime ones. `backend/requirements.txt` drops `deepface`/TensorFlow; they move to a dev-only requirements file alongside documentation on when to re-run the export script.

### `backend/models.py`

Add `challenge_type = Column(String)` to `VerificationSession`.

### `backend/services/auth.py`

`create_verification_session` picks `challenge_type` uniformly at random from `["blink", "turn_left", "turn_right"]` and persists it; `consume_verification_session` returns it unchanged (already returns the full `VerificationSession` row).

### `backend/main.py` / `backend/schemas.py`

- Step-1 response schema gains `challenge_type: str`.
- Step-2 request schema changes `image: str` to `images: List[str]`.

### `backend/config.py`

New env-overridable constants: `LIVENESS_BURST_FRAME_COUNT` (suggested default 13), `LIVENESS_BURST_DURATION_MS` (suggested default 2000), `LIVENESS_MIN_VALID_FRAMES` (suggested default 6), `LIVENESS_MIN_LIVE_FRAME_FRACTION` (suggested default 0.8), `BLINK_EAR_THRESHOLD`, `HEAD_TURN_DISPLACEMENT_THRESHOLD`. All numeric defaults here are starting points to be validated against the real test harness (separate work item), not final tuned values.

### Frontend: `CameraCapture.jsx`

Extended (or split into a new `ChallengeCameraCapture.jsx` to keep the plain single-shot capture used by enrollment separate from the new burst-capture used by step 2 — enrollment doesn't need liveness, it just needs one good photo):

- Reads `challenge_type` from the step-1 response, shows a prompt ("Blink now" / "Turn your head left" / "Turn your head right") with a short countdown.
- On start, captures a frame every ~150ms for ~2s (reusing the existing `webcamRef.getScreenshot()` call in a timed loop instead of capture-on-click) and hands the array to `onCapture`.
- BlazeFace's live "face detected" box is unchanged — still a pure UX hint, now also doubling as "don't bother starting the burst until a face is roughly in frame."

### Frontend: `Login.jsx`

Reads `challenge_type` from the step-1 response, passes it to the capture component, sends the resulting frame array as `images` to step 2.

## Data flow / API contract changes

```
POST /login/step1   (unchanged request)
  -> 200 {session_id, challenge_type, risk: "HIGH"}   (challenge_type is new)

POST /login/step2
  request:  {session_id: str, images: List[str]}      (was: {session_id, image})
  response: unchanged on success (JWT)
            401 "Invalid, expired, or already-used verification session. Please log in again."
              (unchanged message, now also covers PAD failure, gesture failure, and
              face-match failure - deliberately generic, see Error handling)
```

## Error handling & security considerations

- **Uniform failure response.** PAD failure, gesture-mismatch, and face-mismatch all return the same generic message. The client never learns *which* check failed — this prevents an attacker from using the response to iteratively tune an attack (e.g., learning "PAD passed, only gesture failed" would tell them exactly which part of their attack to fix). The specific reason is logged server-side only, for debugging/tuning.
- **Retry issues a fresh random challenge.** Because `VerificationSession` is already single-use, any retry requires a new step-1 call, which assigns a new random `challenge_type`. A captured burst from a failed attempt can never be replayed against a second attempt — it was recorded for a different, now-invalidated prompt.
- **Missing-face frames.** Frames where MediaPipe finds no face are dropped from both the PAD and gesture checks; if fewer than `LIVENESS_MIN_VALID_FRAMES` remain, the request fails with a distinct, non-security "please retake, we couldn't see your face clearly" message (this one *is* safe to surface, since it carries no information about the security checks).
- **Multiple faces in frame.** If more than one face is detected in a frame, treat the request as failed (same generic message) rather than picking one arbitrarily — closes a small tailgating/shoulder-surfing gap that falls out of the per-frame detection loop almost for free.
- **Bandwidth.** ~13 JPEGs at the existing capture resolution (720x540) over 2 seconds is still well under 1MB total — no meaningful change to the network footprint of a login attempt.
- **Alignment correctness is now explicit, not implicit.** DeepFace was performing detection + alignment invisibly; replacing it means `face_match_onnx.align()` must reproduce equivalent alignment quality or face-match accuracy regresses silently. The parity check in `export_arcface_onnx.py` (ONNX output vs. DeepFace/TensorFlow output on the same sample images) is the guard against this at export time; a further end-to-end accuracy check against enrolled users belongs in the test harness work item.

## Testing

- **Unit, `liveness_challenge.py`**: synthetic landmark sequences (crafted EAR curves for blink pass/fail, crafted nose-displacement trajectories for turn_left/turn_right pass/fail, a "no motion at all" case, a "motion too slow / outside the burst window" case). No camera, no model, fast CI.
- **Unit, `face_match_onnx.py`**: parity test loading a handful of sample images, comparing ONNX output to the DeepFace/TensorFlow output captured at export time (fixture, not a live DeepFace call in CI — DeepFace is dev-time only going forward).
- **API-level** (extending the existing suite referenced in the README): correct gesture fulfilled -> pass; wrong gesture performed -> reject; same still image repeated across the whole burst -> PAD rejects (no gesture ever happens); session reuse across a second attempt -> rejects with a freshly different challenge; multi-face frame -> rejects.
- **Manual checklist against a real webcam**: genuine blink/head-turn passes end-to-end; a printed photo tilted in front of the camera fails PAD; a phone replaying a pre-recorded video of the legitimate user fails on gesture-mismatch (the replay can't anticipate the newly-randomized prompt); a different person's face fails face-match (confirms the full chain still works after the serving-path swap).

## Migration notes

- SQLite schema change (`VerificationSession.challenge_type`) — this is a prototype with a dev SQLite file (`backend/mfa_demo.db`), so dropping/recreating the dev DB is acceptable; no production migration tooling is in scope.
- New model artifact `backend/models/arcface.onnx` committed alongside the existing PAD ONNX files (same "small, committed" pattern already established).
- `backend/requirements.txt` loses `deepface` and TensorFlow; a new `backend/requirements-dev.txt` (or equivalent) holds them plus PyTorch, documented as "only needed to regenerate ONNX artifacts," matching the pattern the README already uses for PyTorch today.

## Open questions for implementation planning (not blocking this spec)

- Exact EAR/displacement thresholds and burst timing are starting points; real tuning needs the test-harness work item (own captured faces performing real gestures) rather than guessing numbers here.
- Whether `turn_left`/`turn_right` thresholds need per-device calibration (phone front-camera FOV differs from laptop webcam FOV) is worth watching once real mobile-browser testing starts, but isn't a reason to block this spec.
