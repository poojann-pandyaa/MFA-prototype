# Active Challenge-Response Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-frame step-2 face verification with a randomized challenge-response burst (blink/turn-left/turn-right) verified server-side, and remove TensorFlow/DeepFace from the request path by consolidating all server-side face detection onto MediaPipe Face Landmarker and serving ArcFace via ONNX Runtime.

**Architecture:** Step 1 now also returns a randomly-chosen `challenge_type`, persisted on the existing single-use `VerificationSession` row. Step 2 accepts a burst of frames instead of one image; the server runs one MediaPipe detection pass per frame (bbox + landmarks), then gates face-matching behind two independent checks across the burst — PAD liveness (existing custom model, now sequence-aware) and gesture verification (new, pure-landmark-logic) — before running ArcFace (same weights, now served via ONNX Runtime instead of DeepFace/TensorFlow).

**Tech Stack:** FastAPI, SQLAlchemy/SQLite, `mediapipe` (Face Landmarker Task), `onnxruntime`, OpenCV, pytest + FastAPI `TestClient`; React/Vite frontend, `react-webcam`.

**Spec:** `docs/superpowers/specs/2026-10-01-active-liveness-challenge-design.md`

## Global Constraints

- Web only — no native app or PWA work in this plan.
- Every model/library used must be free, open-source, and require no payment, API key, or approval (no cloud vision APIs).
- TensorFlow and DeepFace must not be imported anywhere in the request path after this plan — dev-time only, used to regenerate `arcface.onnx`.
- All three perception failures (PAD reject, wrong/no gesture, face mismatch) must produce the *exact same* HTTP response (status + body) — no information about which check failed is exposed to the client. "Couldn't see your face" (too few valid frames, or a multi-face frame) is the one exception and may say so.
- A failed or expired attempt always requires a fresh `/login/step1` call, which assigns a brand-new random `challenge_type` — a captured burst is never replayable against a second attempt.
- ArcFace's model/weights are unchanged by this plan — only how they're served changes (DeepFace/TensorFlow → ONNX Runtime). Any accuracy drift must be caught by the parity check in Task 6, not shipped silently.
- Numeric thresholds introduced here (EAR, head-turn displacement, burst size/duration, live-frame fraction) are explicit starting defaults pending calibration against real data (a separate, already-identified work item) — ship them as config defaults, not as hardcoded constants, matching the existing pattern in `backend/config.py`.

## Review Focus

- A step-2 burst where every frame fails face detection (e.g. camera blocked, or a blank/black frame) must return the clean "please retake" response (400), not a 500 crash or a false "verification failed" 401 — tested in Task 7.
- An `images` list shorter than `LIVENESS_MIN_VALID_FRAMES` from the start (before any frames are even dropped) must hit the same clean failure path, not an index or division error — tested in Task 7.
- The `VerificationSession` TTL (90s default) can now expire mid-burst, since capture takes ~2s plus upload time where it previously didn't meaningfully matter — a step-2 call arriving after expiry must still hit the existing, unchanged session-expired 401 correctly — tested in Task 2.
- `/enroll`'s face-embedding extraction currently also runs DeepFace at request time; the "remove TensorFlow from the request path" goal applies there too, not just to login — `/enroll` must keep working identically (same success/failure behavior) after its embedding step moves to the new ONNX path — tested in Task 7.
- PAD-fail, gesture-fail, and face-match-fail must all produce the *identical* 401 response — today's code gives three different statuses/messages (403 "Liveness check failed...", 401 "Face verification failed...") for what becomes three branches of one check, and it's easy to accidentally leave the old differentiated messages in place — tested in Task 8.

---

## Task 1: Pytest test infrastructure

**Files:**
- Create: `backend/conftest.py`
- Create: `backend/requirements-dev.txt`
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/fixtures/README.md`
- Create: `backend/tests/test_smoke.py`

**Interfaces:**
- Produces: a `client` pytest fixture (FastAPI `TestClient` wired to an isolated in-memory SQLite DB via a `get_db` override) and a `db_session` fixture, both importable by every later test file in this plan without redefinition.

- [ ] **Step 1: Create the dev requirements file**

```
# backend/requirements-dev.txt
# Only needed for running tests and regenerating ONNX artifacts (Tasks 1-6
# of docs/superpowers/plans/2026-10-01-active-liveness-challenge.md).
# Never imported at request time - see backend/requirements.txt.
-r requirements.txt
pytest
pytest-mock
```

- [ ] **Step 2: Write `conftest.py`**

```python
# backend/conftest.py
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Base, get_db
import main

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture()
def db_session():
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    main.app.dependency_overrides[get_db] = _override_get_db
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()
```

- [ ] **Step 3: Add a fixtures README instructing a one-time real photo capture**

```markdown
# backend/tests/fixtures/README.md

Several tasks in docs/superpowers/plans/2026-10-01-active-liveness-challenge.md
need one real photo of a face to confirm detection/alignment/liveness work
against an actual human face, not just synthetic arrays. Public datasets
aren't committed here to keep the repo small and license-simple.

Add your own: capture a clear, front-facing, well-lit selfie (webcam or
phone is fine) and save it as `backend/tests/fixtures/sample_face.jpg`.
This file is gitignored (see Task 9) - it's a local manual-testing aid,
not a committed asset.
```

- [ ] **Step 4: Write the smoke test**

```python
# backend/tests/test_smoke.py
def test_history_requires_auth(client):
    response = client.get("/history")
    assert response.status_code == 401
```

- [ ] **Step 5: Run it to confirm the harness works**

Run: `cd backend && pip install -r requirements-dev.txt && pytest tests/test_smoke.py -v`
Expected: PASS. If it errors on `onnxruntime`/`deepface`/other heavy imports in `main.py`'s module-level code, that's expected to work already since those are existing runtime dependencies in `requirements.txt` - the smoke test is validating the *test harness*, not changing any app behavior yet.

- [ ] **Step 6: Commit**

```bash
git add backend/conftest.py backend/requirements-dev.txt backend/tests/
git commit -m "test: add pytest + TestClient harness with isolated in-memory DB"
```

---

## Task 2: `VerificationSession.challenge_type` + randomized assignment

**Files:**
- Modify: `backend/models.py` (add column to `VerificationSession`)
- Modify: `backend/config.py` (add `CHALLENGE_TYPES`)
- Modify: `backend/services/auth.py:64-105` (`create_verification_session`, return type change)
- Modify: `backend/main.py:97-98` (one-line call-site update)
- Create: `backend/tests/test_auth_challenge.py`

**Interfaces:**
- Consumes: `client`, `db_session` fixtures from Task 1.
- Produces: `auth.create_verification_session(db, user, device_identifier) -> tuple[str, str]` (was `-> str`) — returns `(session_id, challenge_type)`. `auth.consume_verification_session(...)` unchanged in signature; the returned `VerificationSession` row now also exposes `.challenge_type`, which Task 8 reads.

- [ ] **Step 1: Write the failing unit test**

```python
# backend/tests/test_auth_challenge.py
import datetime
import config
import models
from services import auth


def _make_user(db_session):
    user = models.User(
        username="alice",
        password_hash=auth.get_password_hash("pw"),
        face_embedding="[]",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_create_verification_session_assigns_known_challenge_type(db_session):
    user = _make_user(db_session)
    for _ in range(30):
        session_id, challenge_type = auth.create_verification_session(db_session, user, "device-1")
        assert challenge_type in config.CHALLENGE_TYPES
        vs = db_session.query(models.VerificationSession).filter_by(session_id=session_id).first()
        assert vs.challenge_type == challenge_type


def test_consume_verification_session_returns_row_with_challenge_type(db_session):
    user = _make_user(db_session)
    session_id, challenge_type = auth.create_verification_session(db_session, user, "device-1")
    vs = auth.consume_verification_session(db_session, session_id, user, "device-1")
    assert vs.challenge_type == challenge_type
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_auth_challenge.py -v`
Expected: FAIL — `VerificationSession` has no `challenge_type` column / `create_verification_session` still returns a plain string.

- [ ] **Step 3: Add the column**

In `backend/models.py`, inside `class VerificationSession(Base):`, after the existing `device_identifier = Column(String)` line, add:

```python
    # Randomly assigned at creation (see services/auth.py); step 2 must
    # verify the user actually performed this specific action, which is
    # what makes a pre-recorded video of the real user fail (see design
    # spec at docs/superpowers/specs/2026-10-01-active-liveness-challenge-design.md).
    challenge_type = Column(String)
```

- [ ] **Step 4: Add `CHALLENGE_TYPES` to config**

In `backend/config.py`, after the `LIVENESS_THRESHOLD` block, add:

```python
# Step-2 challenge-response actions. Randomized per verification session
# so a pre-recorded video of the legitimate user can't anticipate which
# one will be asked for - see liveness_challenge.py for how each is
# verified from the landmark sequence.
CHALLENGE_TYPES = ["blink", "turn_left", "turn_right"]
```

- [ ] **Step 5: Update `create_verification_session`**

In `backend/services/auth.py`, replace:

```python
def create_verification_session(db: Session, user: User, device_identifier: str) -> str:
    """
    Called after a successful step-1 (password) check when step 2 is
    required. Returns a single-use session_id that step 2 must present -
    this is what stops /login/step2 from being callable on its own.
    """
    session_id = uuid.uuid4().hex
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(
        seconds=config.VERIFICATION_SESSION_TTL_SECONDS
    )
    db.add(VerificationSession(
        session_id=session_id,
        user_id=user.id,
        device_identifier=device_identifier,
        expires_at=expires_at,
        consumed=False,
    ))
    db.commit()
    return session_id
```

with:

```python
def create_verification_session(db: Session, user: User, device_identifier: str) -> tuple[str, str]:
    """
    Called after a successful step-1 (password) check when step 2 is
    required. Returns (session_id, challenge_type): a single-use
    session_id that step 2 must present (stops /login/step2 from being
    callable on its own), and the randomly-assigned action the user must
    perform during step 2's capture burst.
    """
    session_id = uuid.uuid4().hex
    challenge_type = random.choice(config.CHALLENGE_TYPES)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(
        seconds=config.VERIFICATION_SESSION_TTL_SECONDS
    )
    db.add(VerificationSession(
        session_id=session_id,
        user_id=user.id,
        device_identifier=device_identifier,
        expires_at=expires_at,
        consumed=False,
        challenge_type=challenge_type,
    ))
    db.commit()
    return session_id, challenge_type
```

Add `import random` to the top of `backend/services/auth.py` alongside the existing `import uuid`.

- [ ] **Step 6: Update the one call site in `main.py`**

In `backend/main.py`, replace:

```python
        session_id = auth.create_verification_session(db, user, login_in.device_identifier)
        return {"require_step2": True, "risk_score": risk_score, "session_id": session_id}
```

with:

```python
        session_id, challenge_type = auth.create_verification_session(db, user, login_in.device_identifier)
        return {
            "require_step2": True,
            "risk_score": risk_score,
            "session_id": session_id,
            "challenge_type": challenge_type,
        }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_auth_challenge.py -v`
Expected: PASS.

- [ ] **Step 8: Add the TTL-expiry-mid-burst regression test (Review Focus item)**

Append to `backend/tests/test_auth_challenge.py`:

```python
def test_consume_fails_after_ttl_expires(db_session, monkeypatch):
    import datetime as dt
    user = _make_user(db_session)
    session_id, _ = auth.create_verification_session(db_session, user, "device-1")

    # Simulate the burst + upload taking longer than the TTL.
    vs = db_session.query(models.VerificationSession).filter_by(session_id=session_id).first()
    vs.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
    db_session.commit()

    import fastapi
    try:
        auth.consume_verification_session(db_session, session_id, user, "device-1")
        assert False, "expected HTTPException"
    except fastapi.HTTPException as e:
        assert e.status_code == 401
```

- [ ] **Step 9: Run full test file**

Run: `cd backend && pytest tests/test_auth_challenge.py -v`
Expected: PASS (3 tests).

- [ ] **Step 10: Confirm the server still boots**

Run: `cd backend && python -c "import main"`
Expected: no errors.

- [ ] **Step 11: Commit**

```bash
git add backend/models.py backend/config.py backend/services/auth.py backend/main.py backend/tests/test_auth_challenge.py
git commit -m "feat: randomize and persist a challenge_type per verification session"
```

---

## Task 3: `face_detect.py` — MediaPipe Face Landmarker wrapper

**Files:**
- Create: `backend/services/face_detect.py`
- Create: `backend/models/face_landmarker.task` (downloaded binary asset)
- Create: `backend/tests/test_face_detect.py`
- Create: `backend/tests/manual_check_face_detect.py`

**Interfaces:**
- Produces: `face_detect.detect(frame_bgr: np.ndarray) -> FrameDetection`, where `FrameDetection` has fields `bbox: Optional[Tuple[int,int,int,int]]` (`left, top, width, height`, same int convention `CropImage.crop()` already expects), `landmarks_px: Optional[np.ndarray]` (shape `(478, 2)`, pixel coordinates), `multi_face: bool`.

- [ ] **Step 1: Download the model asset**

```bash
cd backend/models
curl -L -o face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

If that URL has moved, get the current one from the "Face landmark detection" page at `ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker` (free, Apache-2.0, no key/account needed — same tier as the DeepFace weights already auto-downloaded on first run today).

- [ ] **Step 2: Add `mediapipe` to the runtime requirements**

In `backend/requirements.txt`, add a new line after `onnxruntime`:

```
mediapipe
```

- [ ] **Step 3: Write the failing "no face" test (fully automated, no fixture needed)**

```python
# backend/tests/test_face_detect.py
import numpy as np
from services import face_detect


def test_detect_returns_none_bbox_for_blank_frame():
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    result = face_detect.detect(blank)
    assert result.bbox is None
    assert result.landmarks_px is None
    assert result.multi_face is False
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd backend && pip install -r requirements.txt && pytest tests/test_face_detect.py -v`
Expected: FAIL — `services.face_detect` doesn't exist yet.

- [ ] **Step 5: Implement `face_detect.py`**

```python
# backend/services/face_detect.py
"""
Single server-side face detector + landmark source for the whole app -
PAD cropping, gesture verification, and ArcFace alignment all consume
this module's output instead of each running their own detector (see
"Unified detection" in docs/superpowers/specs/2026-10-01-active-liveness-challenge-design.md).
"""
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python import core as mp_core

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(CURRENT_DIR, "..", "models", "face_landmarker.task")


@dataclass
class FrameDetection:
    bbox: Optional[Tuple[int, int, int, int]]
    landmarks_px: Optional[np.ndarray]
    multi_face: bool


def _build_landmarker():
    base_options = mp_core.base_options.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.IMAGE,
        num_faces=2,  # >1 lets us detect (and reject) multi-face frames
    )
    return mp_vision.FaceLandmarker.create_from_options(options)


_landmarker = _build_landmarker()


def detect(frame_bgr: np.ndarray) -> FrameDetection:
    height, width = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = _landmarker.detect(mp_image)

    faces = result.face_landmarks
    if not faces:
        return FrameDetection(bbox=None, landmarks_px=None, multi_face=False)

    multi_face = len(faces) > 1
    landmarks_px = np.array([[lm.x * width, lm.y * height] for lm in faces[0]], dtype=np.float32)

    xs, ys = landmarks_px[:, 0], landmarks_px[:, 1]
    left, right = int(max(xs.min(), 0)), int(min(xs.max(), width - 1))
    top, bottom = int(max(ys.min(), 0)), int(min(ys.max(), height - 1))
    bbox = (left, top, right - left + 1, bottom - top + 1)

    return FrameDetection(bbox=bbox, landmarks_px=landmarks_px, multi_face=multi_face)
```

- [ ] **Step 6: Run to verify the automated test passes**

Run: `cd backend && pytest tests/test_face_detect.py -v`
Expected: PASS.

- [ ] **Step 7: Write the manual real-face verification script**

```python
# backend/tests/manual_check_face_detect.py
"""
Manual check (needs tests/fixtures/sample_face.jpg - see fixtures/README.md).
Run: cd backend && python tests/manual_check_face_detect.py
"""
import cv2
from services import face_detect

img = cv2.imread("tests/fixtures/sample_face.jpg")
if img is None:
    raise SystemExit("Add tests/fixtures/sample_face.jpg first - see tests/fixtures/README.md")

result = face_detect.detect(img)
print("bbox:", result.bbox)
print("num landmarks:", 0 if result.landmarks_px is None else len(result.landmarks_px))
print("multi_face:", result.multi_face)
assert result.bbox is not None, "expected a face to be detected in sample_face.jpg"
assert result.landmarks_px is not None and len(result.landmarks_px) == 478
print("OK - face detected with landmarks.")
```

- [ ] **Step 8: Run the manual script against your own captured photo**

Add your own `tests/fixtures/sample_face.jpg` per Task 1's `fixtures/README.md`, then:

Run: `cd backend && python tests/manual_check_face_detect.py`
Expected: prints a non-`None` bbox, 478 landmarks, `multi_face: False`, ends with "OK".

- [ ] **Step 9: Commit**

```bash
git add backend/services/face_detect.py backend/requirements.txt backend/tests/test_face_detect.py backend/tests/manual_check_face_detect.py backend/models/face_landmarker.task
git commit -m "feat: add unified MediaPipe Face Landmarker detection service"
```

---

## Task 4: `pad_onnx.py` — detector removed, sequence-aware liveness

**Files:**
- Modify: `backend/services/pad_onnx.py` (remove `_FaceDetector`, `AntiSpoofPredictor.predict` → `predict_with_bbox`, add `check_liveness_sequence`; remove the old single-frame `check_liveness` function)
- Create: `backend/tests/test_pad_onnx.py`

**Interfaces:**
- Consumes: `face_detect.FrameDetection.bbox` (Task 3).
- Produces: `pad_onnx.check_liveness_sequence(frames_bgr: list[np.ndarray], bboxes: list[tuple], threshold: float, min_live_fraction: float) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_pad_onnx.py
import numpy as np
from services import pad_onnx


def test_check_liveness_sequence_empty_frames_is_false():
    assert pad_onnx.check_liveness_sequence([], [], threshold=0.5, min_live_fraction=0.8) is False


def test_check_liveness_sequence_runs_on_blank_frames():
    # Blank frames won't score "live" against a real model, but this must
    # not crash - it's exercising the aggregation logic, not model accuracy.
    frames = [np.zeros((200, 200, 3), dtype=np.uint8) for _ in range(5)]
    bboxes = [(20, 20, 100, 100) for _ in range(5)]
    result = pad_onnx.check_liveness_sequence(frames, bboxes, threshold=0.5, min_live_fraction=0.8)
    assert isinstance(result, bool)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_pad_onnx.py -v`
Expected: FAIL — `check_liveness_sequence` doesn't exist yet.

- [ ] **Step 3: Remove `_FaceDetector` and rework `AntiSpoofPredictor`**

In `backend/services/pad_onnx.py`, delete the entire `class _FaceDetector:` block (lines 55-86) and remove `self.detector = _FaceDetector()` from `AntiSpoofPredictor.__init__`.

Replace the `predict` method:

```python
    def predict(self, img_bgr: np.ndarray):
        """Returns (label, confidence, bbox) or (None, None, None) if no face found."""
        if not self.ready:
            return None, None, None

        bbox = self.detector.get_bbox(img_bgr)
        if bbox is None:
            return None, None, None
        ...
```

with a version that takes the bbox as a parameter instead of detecting it:

```python
    def predict_with_bbox(self, img_bgr: np.ndarray, bbox):
        """bbox is (left, top, width, height) from services.face_detect -
        detection is no longer done here (see "Unified detection" in the
        design spec). Returns (label, confidence) or (None, None) if the
        model isn't ready."""
        if not self.ready:
            return None, None

        if self.custom_model is not None:
            crop = self.cropper.crop(
                org_img=img_bgr, bbox=bbox, scale=CUSTOM_CROP_SCALE,
                out_w=CUSTOM_INPUT_SIZE, out_h=CUSTOM_INPUT_SIZE, crop=True,
            )
            prob_live = self.custom_model.predict_probability(crop)
            label = 1 if prob_live > 0.5 else 0
            return label, prob_live

        prediction = np.zeros((1, 3), dtype=np.float32)
        for model in self.models:
            crop = self.cropper.crop(
                org_img=img_bgr,
                bbox=bbox,
                scale=model.scale,
                out_w=model.w_input,
                out_h=model.h_input,
                crop=model.scale is not None,
            )
            prediction += model.predict(crop)

        label = int(np.argmax(prediction))
        confidence = float(prediction[0][label] / len(self.models))
        return label, confidence
```

(The debug-image-dump block tied to `MFA_DEBUG_PAD_DIR` is dropped here since it was keyed to the old single-frame call site; it can be re-added against `predict_with_bbox` later if needed for burst debugging - not required by this plan.)

- [ ] **Step 4: Replace the old single-frame `check_liveness` with the sequence version**

Replace:

```python
def check_liveness(img_bgr: np.ndarray, threshold: float) -> bool:
    """1 == real/live face label in the underlying MiniFASNet 3-class scheme."""
    if _predictor is None or not _predictor.ready:
        print("Anti-spoofing model not initialized properly.")
        return False

    label, confidence, _ = _predictor.predict(img_bgr)
    if label is None:
        return False

    print(f"Liveness label: {label}, confidence: {confidence:.3f}")
    return label == 1 and confidence > threshold
```

with:

```python
def check_liveness_sequence(frames_bgr, bboxes, threshold: float, min_live_fraction: float) -> bool:
    """1 == real/live face label in the underlying PAD scheme, aggregated
    across a burst: requires at least min_live_fraction of frames to be
    classified live, rather than trusting any single frame (see "PAD
    check across the whole burst" in the design spec)."""
    if not frames_bgr:
        return False
    if _predictor is None or not _predictor.ready:
        print("Anti-spoofing model not initialized properly.")
        return False

    live_count = 0
    for frame, bbox in zip(frames_bgr, bboxes):
        label, confidence = _predictor.predict_with_bbox(frame, bbox)
        if label == 1 and confidence is not None and confidence > threshold:
            live_count += 1

    live_fraction = live_count / len(frames_bgr)
    print(f"Liveness: {live_count}/{len(frames_bgr)} frames live ({live_fraction:.2f})")
    return live_fraction >= min_live_fraction
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_pad_onnx.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite so far**

Run: `cd backend && pytest tests/ -v`
Expected: all PASS (Tasks 1-4's tests).

- [ ] **Step 7: Commit**

```bash
git add backend/services/pad_onnx.py backend/tests/test_pad_onnx.py
git commit -m "refactor: drop pad_onnx's own detector, add sequence-aware liveness check"
```

---

## Task 5: `liveness_challenge.py` — gesture verification from landmarks

**Files:**
- Create: `backend/services/liveness_challenge.py`
- Modify: `backend/config.py` (add gesture thresholds)
- Create: `backend/tests/test_liveness_challenge.py`

**Interfaces:**
- Consumes: a list of `np.ndarray` shape `(478, 2)` pixel-coordinate landmark arrays (one per valid frame, from `face_detect.FrameDetection.landmarks_px`) and a `challenge_type: str` (one of `config.CHALLENGE_TYPES`).
- Produces: `liveness_challenge.verify_gesture(landmark_sequence: list[np.ndarray], challenge_type: str) -> bool`.

- [ ] **Step 1: Add gesture thresholds to config**

In `backend/config.py`, after `CHALLENGE_TYPES`, add:

```python
# Gesture-verification thresholds (services/liveness_challenge.py). These
# are starting defaults, not measured values - calibrate against your own
# captured burst data (see tests/fixtures/README.md) the same way
# LIVENESS_THRESHOLD above was calibrated against ml/eval.py's output.
BLINK_EAR_THRESHOLD = float(os.environ.get("MFA_BLINK_EAR_THRESHOLD", "0.2"))
HEAD_TURN_DISPLACEMENT_THRESHOLD = float(os.environ.get("MFA_HEAD_TURN_DISPLACEMENT_THRESHOLD", "0.15"))
```

- [ ] **Step 2: Write the failing tests using synthetic landmark arrays**

```python
# backend/tests/test_liveness_challenge.py
import numpy as np
import config
from services import liveness_challenge as lc


def _base_landmarks():
    # 478 points, all at origin except the ones verify_gesture actually
    # reads - only those indices matter for these tests.
    lm = np.zeros((478, 2), dtype=np.float32)
    # Eyes open, level, symmetric: horizontal corners wide apart,
    # vertical lid pairs far apart relative to horizontal -> high EAR.
    lm[33] = [100, 100]
    lm[133] = [140, 100]
    lm[159] = [115, 92]
    lm[145] = [115, 108]
    lm[158] = [125, 92]
    lm[153] = [125, 108]
    lm[362] = [200, 100]
    lm[263] = [240, 100]
    lm[386] = [215, 92]
    lm[374] = [215, 108]
    lm[387] = [225, 92]
    lm[373] = [225, 108]
    lm[1] = [170, 120]  # nose tip, centered between the eyes
    return lm


def _closed_eyes(lm):
    lm = lm.copy()
    # Vertical lid pairs collapse together -> low EAR (eyes closed).
    lm[159] = [115, 99]
    lm[145] = [115, 101]
    lm[158] = [125, 99]
    lm[153] = [125, 101]
    lm[386] = [215, 99]
    lm[374] = [215, 101]
    lm[387] = [225, 99]
    lm[373] = [225, 101]
    return lm


def test_blink_sequence_with_dip_and_recovery_passes():
    open_lm = _base_landmarks()
    closed_lm = _closed_eyes(open_lm)
    sequence = [open_lm, open_lm, closed_lm, open_lm, open_lm]
    assert lc.verify_gesture(sequence, "blink") is True


def test_blink_sequence_with_no_dip_fails():
    open_lm = _base_landmarks()
    sequence = [open_lm] * 5
    assert lc.verify_gesture(sequence, "blink") is False


def test_turn_left_sequence_passes():
    lm = _base_landmarks()
    sequence = []
    for i in range(5):
        shifted = lm.copy()
        shifted[1] = [170 + i * 15, 120]  # nose moves steadily toward image-right
        sequence.append(shifted)
    assert lc.verify_gesture(sequence, "turn_left") is True
    assert lc.verify_gesture(sequence, "turn_right") is False


def test_too_short_sequence_fails_closed():
    assert lc.verify_gesture([_base_landmarks()], "blink") is False
    assert lc.verify_gesture([], "blink") is False
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && pytest tests/test_liveness_challenge.py -v`
Expected: FAIL — `services.liveness_challenge` doesn't exist yet.

- [ ] **Step 4: Implement `liveness_challenge.py`**

```python
# backend/services/liveness_challenge.py
"""
Pure landmark-sequence-in, bool-out gesture verification - no I/O, no
model loading, so it's directly unit-testable with synthetic landmark
arrays (see tests/test_liveness_challenge.py). Landmark indices below
are the standard MediaPipe Face Mesh topology indices (478-point model);
if verify_gesture looks miscalibrated against real captures, re-check
these against your installed mediapipe version's canonical face model
before assuming the thresholds are wrong.

Sign convention: turn_left/turn_right compare the FIRST and LAST frame's
nose position (not min/max), so a brief twitch doesn't count - only a
sustained displacement across the whole burst does. Whether a rightward
pixel shift means the subject's "left" or "right" depends on whether the
frames you're capturing are mirrored before this code sees them; confirm
the mapping against your own camera in Task 11's manual check and flip
the sign here if it's backwards for your setup.
"""
import numpy as np
import config

_LEFT_EYE_HORIZONTAL = (33, 133)
_LEFT_EYE_VERTICAL = [(159, 145), (158, 153)]
_RIGHT_EYE_HORIZONTAL = (362, 263)
_RIGHT_EYE_VERTICAL = [(386, 374), (387, 373)]
_NOSE_TIP = 1
_INTER_EYE = (133, 362)

_MIN_FRAMES = 3


def _ear(landmarks: np.ndarray, horizontal, vertical_pairs) -> float:
    h0, h1 = horizontal
    horiz = np.linalg.norm(landmarks[h0] - landmarks[h1])
    if horiz == 0:
        return 0.0
    vert = sum(np.linalg.norm(landmarks[a] - landmarks[b]) for a, b in vertical_pairs)
    vert /= len(vertical_pairs)
    return float(vert / horiz)


def _mean_ear(landmarks: np.ndarray) -> float:
    left = _ear(landmarks, _LEFT_EYE_HORIZONTAL, _LEFT_EYE_VERTICAL)
    right = _ear(landmarks, _RIGHT_EYE_HORIZONTAL, _RIGHT_EYE_VERTICAL)
    return (left + right) / 2


def _verify_blink(sequence) -> bool:
    ears = [_mean_ear(lm) for lm in sequence]
    baseline = max(ears)
    dip = min(ears)
    return dip < config.BLINK_EAR_THRESHOLD and baseline >= config.BLINK_EAR_THRESHOLD


def _verify_turn(sequence, direction: str) -> bool:
    normalized_x = []
    for lm in sequence:
        inter_eye = np.linalg.norm(lm[_INTER_EYE[0]] - lm[_INTER_EYE[1]])
        if inter_eye == 0:
            return False
        normalized_x.append(lm[_NOSE_TIP][0] / inter_eye)

    displacement = normalized_x[-1] - normalized_x[0]
    if direction == "turn_left":
        return displacement > config.HEAD_TURN_DISPLACEMENT_THRESHOLD
    return displacement < -config.HEAD_TURN_DISPLACEMENT_THRESHOLD


def verify_gesture(landmark_sequence, challenge_type: str) -> bool:
    if len(landmark_sequence) < _MIN_FRAMES:
        return False
    if challenge_type == "blink":
        return _verify_blink(landmark_sequence)
    if challenge_type in ("turn_left", "turn_right"):
        return _verify_turn(landmark_sequence, challenge_type)
    return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_liveness_challenge.py -v`
Expected: PASS (4 tests). If `test_blink_sequence_with_dip_and_recovery_passes` fails, print the computed EAR values (`[lc._mean_ear(f) for f in sequence]`) and adjust the synthetic fixture's coordinates until open vs. closed clearly straddles `config.BLINK_EAR_THRESHOLD` — don't change the threshold to match a broken fixture.

- [ ] **Step 6: Commit**

```bash
git add backend/services/liveness_challenge.py backend/config.py backend/tests/test_liveness_challenge.py
git commit -m "feat: add landmark-based gesture verification for blink/turn challenges"
```

---

## Task 6: ArcFace off DeepFace — export script + ONNX serving

**Files:**
- Create: `backend/anti_spoofing/export_arcface_onnx.py`
- Create: `backend/services/face_match_onnx.py`
- Create: `backend/tests/test_face_match_onnx.py`
- Create: `backend/tests/manual_check_face_match.py`

**Interfaces:**
- Produces: `face_match_onnx.align(frame_bgr: np.ndarray, landmarks_px: np.ndarray) -> np.ndarray` (112x112x3 BGR aligned crop), `face_match_onnx.embed(aligned_crop_bgr: np.ndarray) -> np.ndarray`, `face_match_onnx.verify(embedding: np.ndarray, stored_embedding_json: str) -> bool`.

- [ ] **Step 1: Confirm DeepFace's exact ArcFace preprocessing before replicating it**

This determines the normalization `align()`/`embed()` must use — don't guess it. Run:

```bash
cd backend && python -c "
import deepface, os
print(os.path.dirname(deepface.__file__))
"
```

Then read `<that path>/basemodels/ArcFace.py` and `<that path>/modules/preprocessing.py` (or `functions.py`, depending on installed version) to find the exact resize target size and pixel normalization (e.g. `/255`, or mean/std standardization) DeepFace applies before the ArcFace forward pass. Note it in a comment in Step 4 below — Step 7's parity check will numerically confirm whether you got it right.

- [ ] **Step 2: Write the export script**

```python
# backend/anti_spoofing/export_arcface_onnx.py
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
    python anti_spoofing/export_arcface_onnx.py
    # writes backend/models/arcface.onnx
"""
import os

import numpy as np
import tensorflow as tf
import tf2onnx
from deepface import DeepFace

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(CURRENT_DIR, "..", "models", "arcface.onnx")

INPUT_SIZE = 112


def main():
    model_wrapper = DeepFace.build_model("ArcFace")
    keras_model = model_wrapper.model

    input_signature = [tf.TensorSpec([1, INPUT_SIZE, INPUT_SIZE, 3], tf.float32, name="input")]
    onnx_model, _ = tf2onnx.convert.from_keras(
        keras_model, input_signature=input_signature, opset=13, output_path=OUT_PATH,
    )
    print(f"Exported ArcFace -> {OUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add dev-only export dependencies**

In `backend/requirements-dev.txt`, add after `pytest-mock`:

```
tf2onnx
tensorflow
deepface
```

(`tensorflow`/`deepface` are already pulled in transitively by today's `requirements.txt` via `-r requirements.txt`, but Task 9 removes them from there — list them explicitly here so this file stays correct once that happens.)

- [ ] **Step 4: Run the export**

Run: `cd backend && pip install -r requirements-dev.txt && python anti_spoofing/export_arcface_onnx.py`
Expected: `backend/models/arcface.onnx` is created, a few MB in size.

- [ ] **Step 5: Write the failing parity test**

```python
# backend/tests/test_face_match_onnx.py
import json

import numpy as np
import cv2

from services import face_match_onnx


def test_embed_matches_deepface_on_sample_image():
    img_path = "tests/fixtures/sample_face.jpg"
    img = cv2.imread(img_path)
    if img is None:
        import pytest
        pytest.skip("Add tests/fixtures/sample_face.jpg first - see tests/fixtures/README.md")

    from services import face_detect
    detection = face_detect.detect(img)
    assert detection.bbox is not None, "expected a face in sample_face.jpg"

    aligned = face_match_onnx.align(img, detection.landmarks_px)
    onnx_embedding = face_match_onnx.embed(aligned)

    from deepface import DeepFace
    deepface_result = DeepFace.represent(img_path=img, model_name="ArcFace", enforce_detection=True)
    deepface_embedding = np.array(deepface_result[0]["embedding"])

    cosine_sim = np.dot(onnx_embedding, deepface_embedding) / (
        np.linalg.norm(onnx_embedding) * np.linalg.norm(deepface_embedding)
    )
    assert cosine_sim > 0.95, f"ONNX/DeepFace embeddings diverge (cosine_sim={cosine_sim:.3f}) - check align() preprocessing against Step 1's findings"


def test_verify_matches_embedding_against_itself():
    embedding = np.ones(512, dtype=np.float32)
    stored = json.dumps(embedding.tolist())
    assert face_match_onnx.verify(embedding, stored) is True


def test_verify_rejects_dissimilar_embedding():
    embedding = np.ones(512, dtype=np.float32)
    different = -np.ones(512, dtype=np.float32)
    stored = json.dumps(different.tolist())
    assert face_match_onnx.verify(embedding, stored) is False
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd backend && pytest tests/test_face_match_onnx.py -v`
Expected: FAIL — `services.face_match_onnx` doesn't exist yet (and the skip fires if `sample_face.jpg` isn't present yet — add it now per Task 1 if you haven't).

- [ ] **Step 7: Implement `face_match_onnx.py`**

Use the normalization you confirmed in Step 1 — replace the `/ 255.0` below if DeepFace's actual preprocessing differs:

```python
# backend/services/face_match_onnx.py
"""
ArcFace alignment + embedding + matching via ONNX Runtime - replaces
DeepFace at request time (see backend/anti_spoofing/export_arcface_onnx.py
for how arcface.onnx is produced; same weights, different serving path).
"""
import json
import os

import cv2
import numpy as np
import onnxruntime as ort

import config

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(CURRENT_DIR, "..", "models", "arcface.onnx")
ALIGNED_SIZE = 112

# Standard ArcFace alignment reference points (112x112 canonical template,
# same one insightface's face_align.norm_crop uses) - left eye, right eye,
# nose, left mouth corner, right mouth corner.
_REFERENCE_POINTS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)

# MediaPipe Face Mesh indices for the same 5 semantic points.
_LANDMARK_INDICES = {
    "left_eye": 133,
    "right_eye": 362,
    "nose": 1,
    "mouth_left": 61,
    "mouth_right": 291,
}

_session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
_input_name = _session.get_inputs()[0].name


def align(frame_bgr: np.ndarray, landmarks_px: np.ndarray) -> np.ndarray:
    src = np.array([
        landmarks_px[_LANDMARK_INDICES["left_eye"]],
        landmarks_px[_LANDMARK_INDICES["right_eye"]],
        landmarks_px[_LANDMARK_INDICES["nose"]],
        landmarks_px[_LANDMARK_INDICES["mouth_left"]],
        landmarks_px[_LANDMARK_INDICES["mouth_right"]],
    ], dtype=np.float32)

    transform, _ = cv2.estimateAffinePartial2D(src, _REFERENCE_POINTS, method=cv2.LMEDS)
    aligned = cv2.warpAffine(frame_bgr, transform, (ALIGNED_SIZE, ALIGNED_SIZE), borderValue=0.0)
    return aligned


def embed(aligned_crop_bgr: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(aligned_crop_bgr, cv2.COLOR_BGR2RGB)
    img = rgb.astype(np.float32) / 255.0
    img = img[np.newaxis, ...]
    output = _session.run(None, {_input_name: img})[0]
    return output[0]


def verify(embedding: np.ndarray, stored_embedding_str: str) -> bool:
    if not stored_embedding_str:
        return False
    stored = np.array(json.loads(stored_embedding_str))
    cosine_distance = 1 - np.dot(stored, embedding) / (
        np.linalg.norm(stored) * np.linalg.norm(embedding)
    )
    return cosine_distance < config.FACE_MATCH_THRESHOLD
```

- [ ] **Step 8: Run the parity + verify tests**

Run: `cd backend && pytest tests/test_face_match_onnx.py -v`
Expected: PASS. If the parity test fails with a low cosine similarity, re-check the normalization against Step 1's findings (most likely culprit: wrong pixel scaling, or RGB/BGR order) rather than loosening the 0.95 threshold.

- [ ] **Step 9: Write a manual two-photo check**

```python
# backend/tests/manual_check_face_match.py
"""
Manual check: confirm face_match_onnx distinguishes two different real
people, not just passing a parity test against itself.
Needs tests/fixtures/sample_face.jpg (you) and a second photo of a
different person saved as tests/fixtures/sample_face_other.jpg.
Run: cd backend && python tests/manual_check_face_match.py
"""
import cv2
from services import face_detect, face_match_onnx

def _embed(path):
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"Missing {path}")
    detection = face_detect.detect(img)
    assert detection.bbox is not None, f"no face found in {path}"
    aligned = face_match_onnx.align(img, detection.landmarks_px)
    return face_match_onnx.embed(aligned)

mine = _embed("tests/fixtures/sample_face.jpg")
other = _embed("tests/fixtures/sample_face_other.jpg")

import numpy as np
cosine_distance = 1 - np.dot(mine, other) / (np.linalg.norm(mine) * np.linalg.norm(other))
print(f"cosine_distance between two different people: {cosine_distance:.3f}")
import config
print(f"FACE_MATCH_THRESHOLD: {config.FACE_MATCH_THRESHOLD} (distance should be ABOVE this for different people)")
```

- [ ] **Step 10: Run it against your own two photos**

Run: `cd backend && python tests/manual_check_face_match.py`
Expected: printed `cosine_distance` comfortably above `config.FACE_MATCH_THRESHOLD` (0.45 default). If it's close to or below the threshold, alignment or preprocessing likely needs another look before trusting this path for real logins.

- [ ] **Step 11: Commit**

```bash
git add backend/anti_spoofing/export_arcface_onnx.py backend/services/face_match_onnx.py backend/requirements-dev.txt backend/tests/test_face_match_onnx.py backend/tests/manual_check_face_match.py backend/models/arcface.onnx
git commit -m "feat: serve ArcFace via ONNX Runtime, replacing DeepFace at request time"
```

---

## Task 7: `biometrics.py` orchestration + `/enroll` off DeepFace

**Files:**
- Modify: `backend/services/biometrics.py` (replace `check_liveness`/`verify_face`/`extract_embedding` with the new orchestration; remove the module's lazy DeepFace imports)
- Modify: `backend/config.py` (burst-size tunables)
- Create: `backend/tests/test_biometrics.py`

**Interfaces:**
- Consumes: `face_detect.detect`, `pad_onnx.check_liveness_sequence`, `liveness_challenge.verify_gesture`, `face_match_onnx.align/embed/verify` (Tasks 3-6).
- Produces: `biometrics.verify_liveness_and_identity(images_b64: list[str], challenge_type: str, stored_embedding: str) -> bool` (raises `biometrics.RetakeNeededError` for the "couldn't see your face" case); `biometrics.extract_embedding(face_image_b64: str) -> str` (same name/signature as today, reimplemented on the ONNX path, used by `/enroll` — Task 8 does not need to change for this).

- [ ] **Step 1: Add burst-size config**

In `backend/config.py`, after the gesture thresholds, add:

```python
# Step-2 capture burst (frontend captures this many frames; see Task 11's
# ChallengeCameraCapture.jsx). Starting defaults, not measured values.
LIVENESS_MIN_VALID_FRAMES = int(os.environ.get("MFA_LIVENESS_MIN_VALID_FRAMES", "6"))
LIVENESS_MIN_LIVE_FRAME_FRACTION = float(os.environ.get("MFA_LIVENESS_MIN_LIVE_FRAME_FRACTION", "0.8"))
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_biometrics.py
import base64
import json
from unittest import mock

import cv2
import numpy as np
import pytest

from services import biometrics, face_detect


def _blank_frames_b64(n):
    blank = np.zeros((200, 200, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    b64 = base64.b64encode(buf).decode("utf-8")
    return [b64] * n


def test_too_few_valid_frames_raises_retake_needed():
    frames = _blank_frames_b64(3)  # below LIVENESS_MIN_VALID_FRAMES=6, and blank = no face anyway
    with pytest.raises(biometrics.RetakeNeededError):
        biometrics.verify_liveness_and_identity(frames, "blink", json.dumps([0.0] * 512))


def test_short_images_list_raises_retake_needed_not_crash():
    frames = _blank_frames_b64(1)
    with pytest.raises(biometrics.RetakeNeededError):
        biometrics.verify_liveness_and_identity(frames, "blink", json.dumps([0.0] * 512))


def test_extract_embedding_handles_blank_image_gracefully():
    blank_b64 = _blank_frames_b64(1)[0]
    # No face in a blank frame -> empty string, same contract as before.
    assert biometrics.extract_embedding(blank_b64) == ""


def test_extract_embedding_returns_real_embedding_for_real_face():
    # /enroll's embedding extraction moved off DeepFace onto the ONNX path
    # in this task - confirm it still produces a usable embedding for a
    # real face, not just that it fails gracefully on blank input above.
    img = cv2.imread("tests/fixtures/sample_face.jpg")
    if img is None:
        pytest.skip("Add tests/fixtures/sample_face.jpg first - see tests/fixtures/README.md")
    ok, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf).decode("utf-8")

    result = biometrics.extract_embedding(b64)
    assert result != ""
    embedding = json.loads(result)
    assert len(embedding) == 512


def test_multi_face_frame_fails_generic_not_retake():
    # A multi-face frame is a possible attack signal (someone else in
    # frame, or a photo held in front of the attacker's own face) - it
    # must fail the same generic way PAD/gesture/face-match do, NOT take
    # the benign "please retake" path a simple no-face frame would.
    frames = _blank_frames_b64(8)
    fake_detection = face_detect.FrameDetection(
        bbox=(10, 10, 50, 50),
        landmarks_px=np.zeros((478, 2), dtype=np.float32),
        multi_face=True,
    )
    with mock.patch.object(face_detect, "detect", return_value=fake_detection):
        result = biometrics.verify_liveness_and_identity(frames, "blink", json.dumps([0.0] * 512))
    assert result is False
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && pytest tests/test_biometrics.py -v`
Expected: FAIL — `verify_liveness_and_identity`/`RetakeNeededError` don't exist yet.

- [ ] **Step 4: Rewrite `biometrics.py`**

Replace the entire file:

```python
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
    challenge_type, and face-match on the best remaining frame. Returns
    True only if all three pass; raises RetakeNeededError if too few
    frames had a single detectable face."""
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

    if not pad_onnx.check_liveness_sequence(
        valid_frames, bboxes, config.LIVENESS_THRESHOLD, config.LIVENESS_MIN_LIVE_FRAME_FRACTION
    ):
        return False

    landmark_sequence = [d.landmarks_px for d in valid_detections]
    if not liveness_challenge.verify_gesture(landmark_sequence, challenge_type):
        return False

    best_idx = max(
        range(len(valid_detections)),
        key=lambda i: valid_detections[i].bbox[2] * valid_detections[i].bbox[3],
    )
    best_frame = valid_frames[best_idx]
    best_detection = valid_detections[best_idx]

    aligned = face_match_onnx.align(best_frame, best_detection.landmarks_px)
    embedding = face_match_onnx.embed(aligned)
    return face_match_onnx.verify(embedding, stored_embedding)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_biometrics.py -v`
Expected: PASS (5 tests, or 4 PASS + 1 SKIP if `tests/fixtures/sample_face.jpg` isn't present yet).

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/services/biometrics.py backend/config.py backend/tests/test_biometrics.py
git commit -m "feat: orchestrate burst-based liveness+gesture+face-match; move /enroll off DeepFace"
```

---

## Task 8: API contract wiring — `schemas.py` + `main.py`

**Files:**
- Modify: `backend/schemas.py:15-19` (`UserLoginStep2.images: List[str]`)
- Modify: `backend/main.py:100-138` (`login_step2`)
- Create: `backend/tests/test_login_flow.py`

**Interfaces:**
- Consumes: `biometrics.verify_liveness_and_identity`, `biometrics.RetakeNeededError` (Task 7); `auth.consume_verification_session` returning a row with `.challenge_type` (Task 2).

- [ ] **Step 1: Update the schema**

In `backend/schemas.py`, replace:

```python
class UserLoginStep2(BaseModel):
    username: str
    face_image_b64: str
    device_identifier: str
    session_id: str  # returned by /login/step1; proves step 1 was completed
```

with:

```python
class UserLoginStep2(BaseModel):
    username: str
    images: List[str]
    device_identifier: str
    session_id: str  # returned by /login/step1; proves step 1 was completed
```

- [ ] **Step 2: Write the failing API-level tests**

These monkeypatch `biometrics.verify_liveness_and_identity` to isolate the *orchestration* behavior (session binding, status codes, uniform error messages) from real ML correctness, which Tasks 3-7's own unit/manual tests already cover separately — see the design spec's testing section for why this split is intentional.

```python
# backend/tests/test_login_flow.py
import models
from services import auth, biometrics


def _enroll_user(client, username="alice", password="hunter2"):
    response = client.post("/enroll", json={
        "username": username,
        "password": password,
        "face_image_b64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    })
    return response


def _force_high_risk_session(client, db_session, username, device_identifier):
    # calculate_risk_score returns HIGH for any never-before-seen device,
    # which is already true for a freshly enrolled user's first login.
    response = client.post("/login/step1", json={
        "username": username, "password": "hunter2", "device_identifier": device_identifier,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["require_step2"] is True
    assert body["challenge_type"] in ["blink", "turn_left", "turn_right"]
    return body["session_id"], body["challenge_type"]


def test_step1_returns_challenge_type(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, challenge_type = _force_high_risk_session(client, db_session, "alice", "device-x")
    assert session_id
    assert challenge_type


def test_step2_generic_failure_message_same_for_pad_gesture_and_match(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)

    for outcome_name, side_effect in [
        ("pad_fail", False),
        ("gesture_fail", False),
        ("match_fail", False),
    ]:
        session_id, _ = _force_high_risk_session(client, db_session, "alice", f"device-{outcome_name}")
        monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: side_effect)
        response = client.post("/login/step2", json={
            "username": "alice",
            "images": ["irrelevant-because-mocked"],
            "device_identifier": f"device-{outcome_name}",
            "session_id": session_id,
        })
        assert response.status_code == 401
        assert response.json()["detail"] == "Verification failed. Please try again."


def test_step2_retake_needed_returns_400_with_distinct_message(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-retake")

    def _raise_retake(*a, **k):
        raise biometrics.RetakeNeededError("Could not clearly see a single face across enough frames. Please retake.")

    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", _raise_retake)
    response = client.post("/login/step2", json={
        "username": "alice", "images": ["x"], "device_identifier": "device-retake", "session_id": session_id,
    })
    assert response.status_code == 400
    assert "retake" in response.json()["detail"].lower()


def test_step2_success_issues_token(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-ok")
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)

    response = client.post("/login/step2", json={
        "username": "alice", "images": ["x"], "device_identifier": "device-ok", "session_id": session_id,
    })
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_step2_rejects_without_consuming_session_twice(client, db_session, monkeypatch):
    monkeypatch.setattr(biometrics, "extract_embedding", lambda b64: "[]")
    _enroll_user(client)
    session_id, _ = _force_high_risk_session(client, db_session, "alice", "device-replay")
    monkeypatch.setattr(biometrics, "verify_liveness_and_identity", lambda *a, **k: True)

    payload = {"username": "alice", "images": ["x"], "device_identifier": "device-replay", "session_id": session_id}
    first = client.post("/login/step2", json=payload)
    assert first.status_code == 200
    second = client.post("/login/step2", json=payload)
    assert second.status_code == 401
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && pytest tests/test_login_flow.py -v`
Expected: FAIL — `main.py` still expects `face_image_b64` and still raises the old differentiated 403/401 messages.

- [ ] **Step 4: Rewrite `login_step2`**

In `backend/main.py`, replace:

```python
@app.post("/login/step2")
def login_step2(login_in: schemas.UserLoginStep2, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == login_in.username).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # 0. Require proof that step 1 (password) was already completed for
    # this exact user+device. Without this check, step 2 would be a full
    # MFA bypass: anyone who can pass the face check for a *known username*
    # would get a token with no password at all.
    auth.consume_verification_session(db, login_in.session_id, user, login_in.device_identifier)

    # 1. Liveness check
    is_live = biometrics.check_liveness(login_in.face_image_b64)
    if not is_live:
        raise HTTPException(status_code=403, detail="Liveness check failed. Spoofing detected.")

    # 2. Face matching
    is_match = biometrics.verify_face(login_in.face_image_b64, user.face_embedding)
    if not is_match:
        raise HTTPException(status_code=401, detail="Face verification failed.")

    # If passes:
```

with:

```python
@app.post("/login/step2")
def login_step2(login_in: schemas.UserLoginStep2, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == login_in.username).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # Require proof that step 1 (password) was already completed for this
    # exact user+device. Without this check, step 2 would be a full MFA
    # bypass: anyone who can pass the face check for a *known username*
    # would get a token with no password at all.
    vs = auth.consume_verification_session(db, login_in.session_id, user, login_in.device_identifier)

    # PAD, gesture, and face-match all live behind one generic failure
    # message - a client (or attacker) never learns which one failed, so
    # a captured burst can't be iteratively tuned against the checks (see
    # "Error handling & security considerations" in the design spec).
    try:
        verified = biometrics.verify_liveness_and_identity(
            login_in.images, vs.challenge_type, user.face_embedding
        )
    except biometrics.RetakeNeededError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not verified:
        raise HTTPException(status_code=401, detail="Verification failed. Please try again.")

    # If passes:
```

(Leave the rest of the function — the history/device/token block — unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_login_flow.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Run the full backend test suite**

Run: `cd backend && pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/schemas.py backend/main.py backend/tests/test_login_flow.py
git commit -m "feat: wire burst-based step-2 request/response and uniform failure messages"
```

---

## Task 9: Dependency split + README updates

**Files:**
- Modify: `backend/requirements.txt` (remove `deepface`, `tf-keras`; add `mediapipe`, `onnxruntime` already present)
- Modify: `.gitignore` (ignore `backend/tests/fixtures/*.jpg`)
- Modify: `README.md` (architecture section + setup instructions)
- Delete: `backend/test_deepface.py`, `backend/test_verify.py` (superseded by `backend/tests/`)

- [ ] **Step 1: Trim runtime requirements**

In `backend/requirements.txt`, remove the `deepface` and `tf-keras` lines (DeepFace/TensorFlow are now dev-only, see `requirements-dev.txt`). Keep everything else, including the `mediapipe` line added in Task 3.

- [ ] **Step 2: Ignore local test fixtures**

In `.gitignore` (create it at the repo root if it doesn't exist, or check `backend/.gitignore` conventions first), add:

```
backend/tests/fixtures/*.jpg
backend/tests/fixtures/*.jpeg
backend/tests/fixtures/*.png
```

- [ ] **Step 3: Remove the superseded manual smoke scripts**

```bash
git rm backend/test_deepface.py backend/test_verify.py
```

(These were ad-hoc DeepFace smoke scripts with no assertions, hitting the network for sample images — fully superseded by `backend/tests/`.)

- [ ] **Step 4: Update README's architecture + setup sections**

In `README.md`, update the **Architecture** section's bullet list to reflect the new flow (challenge-response liveness, unified MediaPipe detection, ArcFace via ONNX Runtime) and update **Setup Instructions** to mention `requirements-dev.txt` for regenerating ONNX artifacts and running tests, plus the `backend/tests/fixtures/README.md` instruction to add a personal photo for manual checks. Keep the existing security-fixes and project-structure sections, amending file references that changed (e.g. `pad_onnx.py`'s detector removal, the new `services/face_detect.py`, `services/liveness_challenge.py`, `services/face_match_onnx.py`).

- [ ] **Step 5: Verify a clean install still works**

Run: `cd backend && rm -rf .venv_check && python3 -m venv .venv_check && source .venv_check/bin/activate && pip install -r requirements.txt && python -c "import main" && deactivate && rm -rf .venv_check`
Expected: no import errors, and no `tensorflow`/`deepface` packages get installed (confirm with `pip show deepface` failing inside that venv before deactivating, if you want extra confidence).

- [ ] **Step 6: Run the full test suite one more time**

Run: `cd backend && pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt .gitignore README.md
git rm backend/test_deepface.py backend/test_verify.py
git commit -m "chore: remove TensorFlow/DeepFace from runtime deps, update docs"
```

---

## Task 10: `ChallengeCameraCapture.jsx` — frontend burst capture

**Files:**
- Create: `frontend/src/components/ChallengeCameraCapture.jsx`

**Interfaces:**
- Produces: a component with props `{ challengeType: string, onCapture: (images: string[]) => void, error, onErrorClear }`, mirroring `CameraCapture.jsx`'s existing prop shape so `Login.jsx` (Task 11) swaps components with minimal churn. `CameraCapture.jsx` itself is untouched — `Enroll.jsx` keeps using it for its single-shot capture, which has no liveness requirement.

- [ ] **Step 1: Implement the component**

```jsx
// frontend/src/components/ChallengeCameraCapture.jsx
import React, { useRef, useState, useCallback, useEffect } from 'react';
import Webcam from 'react-webcam';
import { Camera } from 'lucide-react';
import * as tf from '@tensorflow/tfjs';
import * as blazeface from '@tensorflow-models/blazeface';

const CHALLENGE_PROMPTS = {
  blink: 'Blink naturally',
  turn_left: 'Turn your head to your left',
  turn_right: 'Turn your head to your right',
};

const BURST_FRAME_COUNT = 13;
const BURST_INTERVAL_MS = 150;

const ChallengeCameraCapture = ({ challengeType, onCapture, label = 'Verify Identity', error, onErrorClear }) => {
  const webcamRef = useRef(null);
  const [isFaceDetected, setIsFaceDetected] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [framesCaptured, setFramesCaptured] = useState(0);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let detector = null;
    let animationFrameId = null;

    const loadModelAndDetect = async () => {
      try {
        await tf.ready();
        detector = await blazeface.load();
        detectFace();
      } catch (err) {
        console.error('Failed to load face detection model', err);
      }
    };

    const detectFace = async () => {
      if (
        webcamRef.current &&
        webcamRef.current.video &&
        webcamRef.current.video.readyState === 4 &&
        !capturing &&
        !done
      ) {
        const video = webcamRef.current.video;
        const predictions = await detector.estimateFaces(video, false);
        setIsFaceDetected(predictions.length > 0);
      }
      animationFrameId = requestAnimationFrame(detectFace);
    };

    loadModelAndDetect();
    return () => {
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, [capturing, done]);

  const startBurst = useCallback(() => {
    if (onErrorClear) onErrorClear();
    setCapturing(true);
    setFramesCaptured(0);
    const frames = [];
    const intervalId = setInterval(() => {
      const shot = webcamRef.current?.getScreenshot();
      if (shot) {
        frames.push(shot);
        setFramesCaptured(frames.length);
      }
      if (frames.length >= BURST_FRAME_COUNT) {
        clearInterval(intervalId);
        setCapturing(false);
        setDone(true);
        onCapture(frames);
      }
    }, BURST_INTERVAL_MS);
  }, [onCapture, onErrorClear]);

  let borderColor = 'border-blue-400/50';
  if (error) borderColor = 'border-red-500';
  else if (isFaceDetected) borderColor = 'border-green-500';

  return (
    <div className="flex flex-col items-center space-y-4 w-full">
      <div className="relative w-full max-w-sm rounded-lg overflow-hidden bg-gray-100 aspect-[4/3] flex items-center justify-center">
        <Webcam
          audio={false}
          ref={webcamRef}
          screenshotFormat="image/jpeg"
          videoConstraints={{ width: 720, height: 540, facingMode: 'user' }}
          className="absolute inset-0 w-full h-full object-cover"
        />
        <div className={`absolute inset-0 pointer-events-none flex items-center justify-center border-4 border-dashed rounded-lg m-4 transition-colors duration-200 ${borderColor}`}>
          <span className="bg-black/50 text-white px-3 py-1 rounded text-sm mt-48 text-center">
            {done
              ? 'Captured - verifying...'
              : capturing
                ? `${CHALLENGE_PROMPTS[challengeType] || 'Hold still'} (${framesCaptured}/${BURST_FRAME_COUNT})`
                : isFaceDetected
                  ? `Ready - ${CHALLENGE_PROMPTS[challengeType] || 'press start'}`
                  : 'Position face here'}
          </span>
        </div>
      </div>

      {!capturing && !done && (
        <button
          type="button"
          onClick={startBurst}
          disabled={!isFaceDetected}
          className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          <Camera size={18} />
          <span>{label}</span>
        </button>
      )}
    </div>
  );
};

export default ChallengeCameraCapture;
```

- [ ] **Step 2: Verify it builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no new errors (this component isn't wired into any page yet — Task 11 does that — so this step only confirms it's syntactically valid and typechecks against existing deps).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ChallengeCameraCapture.jsx
git commit -m "feat: add burst-capture camera component for challenge-response liveness"
```

---

## Task 11: Wire `Login.jsx` to the challenge flow + manual end-to-end check

**Files:**
- Modify: `frontend/src/pages/Login.jsx`

- [ ] **Step 1: Update state and step-1 handling**

In `frontend/src/pages/Login.jsx`, add a `challengeType` state next to the existing `sessionId` state:

```jsx
  const [sessionId, setSessionId] = useState(null);
  const [challengeType, setChallengeType] = useState(null);
```

In `handleStep1`, update the destructuring and the `require_step2` branch:

```jsx
      const { require_step2, access_token, risk_score, session_id, challenge_type } = response.data;
      setRiskInfo(risk_score);

      if (require_step2) {
        setSessionId(session_id);
        setChallengeType(challenge_type);
        setStep(2);
```

- [ ] **Step 2: Swap the capture component and request body**

Replace the import:

```jsx
import ChallengeCameraCapture from '../components/ChallengeCameraCapture';
```

Replace `handleStep2`'s body/request:

```jsx
  const handleStep2 = async (imagesB64) => {
    setLoading(true);
    setError('');
    try {
      const response = await axios.post(`${API_URL}/login/step2`, {
        username,
        images: imagesB64,
        device_identifier: getDeviceId(),
        session_id: sessionId
      });
```

Replace the `<CameraCapture ... />` usage:

```jsx
          <ChallengeCameraCapture
            challengeType={challengeType}
            onCapture={handleStep2}
            label={loading ? 'Verifying...' : 'Verify Identity'}
            error={error}
            onErrorClear={() => setError('')}
          />
```

- [ ] **Step 3: Verify it builds**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Login.jsx
git commit -m "feat: wire Login.jsx to burst-based challenge-response capture"
```

- [ ] **Step 5: Manual end-to-end check (needs both servers running)**

Run the backend (`cd backend && uvicorn main:app --reload`) and frontend (`cd frontend && npm run dev`), then from a real browser against a real webcam, walk the spec's full manual checklist:

1. Enroll a test user.
2. Log in from a "new device" (clear `localStorage` or use a private window) to force step 2.
3. Confirm the prompt matches one of "Blink naturally" / "Turn your head to your left" / "Turn your head to your right", and perform it for real — login should succeed.
4. Log in again (new session, likely a different random prompt) and deliberately perform the *wrong* gesture — should fail with "Verification failed. Please try again."
5. Hold a printed (or phone-displayed) photo of your own face up to the camera instead of your real face, following whatever prompt appears — should fail (PAD).
6. Cover the camera / point it at a blank wall during the burst — should fail with the distinct "couldn't clearly see your face... please retake" message, not the generic one.
7. If `turn_left`/`turn_right` consistently fail even when performed correctly, the sign convention noted in `liveness_challenge.py` is likely flipped for your camera's mirroring — swap the `>`/`<` comparison in `_verify_turn` and retest.

---

## Task 12: Full backend test suite + branch sanity pass

**Files:** none (verification only)

- [ ] **Step 1: Run the complete backend test suite**

Run: `cd backend && pytest tests/ -v`
Expected: all tests across Tasks 1-9 PASS.

- [ ] **Step 2: Confirm no TensorFlow/DeepFace import in the request path**

Run: `cd backend && python -c "
import sys
import main
assert 'tensorflow' not in sys.modules, 'TensorFlow got imported at server startup'
assert 'deepface' not in sys.modules, 'DeepFace got imported at server startup'
print('OK - no TensorFlow/DeepFace in the request path')
"`
Expected: prints "OK".

- [ ] **Step 3: Confirm the frontend still builds clean**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 4: Request final review**

Once Steps 1-3 pass and Task 11's manual checklist has been walked for real, use the `superpowers:requesting-code-review` skill (or your chosen execution method's built-in review step) against the full branch diff before merging.
