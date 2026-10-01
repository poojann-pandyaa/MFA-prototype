# Adaptive MFA Demo

This is a working demo of an Adaptive Multi-Factor Authentication (MFA) web application using face recognition and liveness detection.

## Architecture

*   **Backend**: Python, FastAPI, SQLite
*   **Frontend**: React (Vite, TailwindCSS)
*   **Biometrics** (no TensorFlow/DeepFace at request time - everything below runs on MediaPipe and ONNX Runtime):
    *   **Unified face detection**: a single MediaPipe Face Landmarker (`backend/services/face_detect.py`, model file `backend/models/face_landmarker.task`) is the only detector in the app. Its bounding box and 478 landmarks feed PAD cropping, gesture verification, and ArcFace alignment, so no stage runs its own detector. Pinned to `mediapipe==0.10.35` (see the comment in `requirements.txt`).
    *   **Challenge-response liveness** (`backend/services/liveness_challenge.py`): step 2 asks for a randomly chosen action (`blink`, `turn_left`, or `turn_right`) and the client sends a short burst of frames. The landmark sequence across the burst is checked for the requested gesture (eyes open -> closed -> open again for blink; for head turns, a sustained change in the nose tip's position *relative to the eyes*, so sliding or leaning toward the camera without turning doesn't count). A static photo fails, and so does a replay that doesn't happen to show the requested action (see the known-limitations note below for how far that goes).
    *   **Liveness / anti-spoofing (PAD)**: a MobileNetV3-Small model served via **ONNX Runtime** (`backend/services/pad_onnx.py`), applied across the burst (a minimum fraction of frames must score live). Ships today with the vendored, pretrained minivision-ai/Silent-Face-Anti-Spoofing (MiniFASNet) weights converted to ONNX (`backend/anti_spoofing/export_onnx.py`); `ml/` contains a from-scratch **trainable** replacement for this model (multi-task: binary live/spoof + attack type + an auxiliary FFT-artifact head) that you can train on free data and drop in to replace it - see `ml/README.md`. `pad_onnx.py` no longer has its own face detector; it crops using the bounding boxes from `face_detect.py`.
    *   **Face matching**: ArcFace (pretrained, not trained in this repo - see `ml/README.md` for why) served via **ONNX Runtime** (`backend/services/face_match_onnx.py`: landmark-based alignment, embedding, cosine-distance compare). The ONNX file is exported once from DeepFace's ArcFace weights (see Setup) and is not committed (~137MB).
    *   `backend/services/biometrics.py` orchestrates the above for `/enroll` (single photo, embedding only) and `/login/step2` (burst: detect, PAD, gesture, then face-match on every frame PAD scored live - all of them must match).

## Authentication Flow

1.  **Enrollment**: The user provides a username, password, and captures a face photo via their webcam. The backend extracts the face embedding (a mathematical representation of the face) using ArcFace via ONNX Runtime and stores only the embedding and a hashed password in the SQLite database.
2.  **Login Step 1**: The user enters their username and password. If correct, the backend calculates a risk score and, if step 2 is required, issues a short-lived, single-use `session_id` bound to that user+device.
    *   If it's a known device and the login time is within normal bounds, the risk score is **LOW**, and the user is logged in directly.
    *   If it's a new device or unusual time, the risk score is **HIGH** (or MEDIUM), and Step 2 is triggered.
3.  **Login Step 2 (Adaptive)**: The `/login/step1` response includes the `session_id` and a randomly chosen `challenge_type` (`blink`, `turn_left`, or `turn_right`) stored with that session. The user performs the action while the client captures a burst of frames, which is posted to `/login/step2` as `images`. The request must include the `session_id` from step 1 - without a valid, unexpired, not-yet-used session bound to the same user and device, the server rejects the request before even looking at the images. This is what stops step 2 from being callable as a bypass of the password step (see "Security fixes" below). The burst is capped at `LIVENESS_MAX_FRAMES` frames (default 30, env `MFA_LIVENESS_MAX_FRAMES`); larger bursts are rejected with 400 before anything is decoded, so the frontend's burst size must stay at or below 30 (or the env var must be raised to match).
    *   **Face detection**: every frame goes through the MediaPipe Face Landmarker. Frames with more than one face fail verification; if fewer than `MFA_LIVENESS_MIN_VALID_FRAMES` (default 6) frames contain exactly one detectable face, the user is asked to retake.
    *   **Liveness Check**: the burst is passed through the anti-spoofing model (ONNX Runtime); at least `MFA_LIVENESS_MIN_LIVE_FRAME_FRACTION` (default 0.8) of frames must score above `MFA_LIVENESS_THRESHOLD` (default 0.5).
    *   **Gesture Check**: the landmark sequence must show the requested blink or head turn (`MFA_BLINK_EAR_THRESHOLD`, `MFA_HEAD_TURN_DISPLACEMENT_THRESHOLD`).
    *   **Face Matching**: every frame that PAD scored live is aligned and embedded with ArcFace (ONNX Runtime) and compared against the stored enrollment embedding (cosine distance below `MFA_FACE_MATCH_THRESHOLD`, default 0.45); every one of them must match. Frames PAD rejected are never used for matching, so splicing a frame of someone else's photo into an otherwise-live burst can't carry the identity check. If everything passes, the user is logged in.
    *   Liveness, gesture, and face-match failures - and any unexpected server error during verification, which is logged server-side - all return the same generic 401 message, so a client can't learn which check failed (or how far its burst got) and tune an attack against it. Every step-2 attempt consumes its `session_id`, pass or fail; a retry starts again from step 1 with a fresh challenge.
    *   **Request size**: each frame is capped at 1,000,000 base64 characters (`backend/schemas.py`, oversized frames get a 422; the frontend's 720x540 JPEGs are well under that). Also cap the overall request body at your reverse proxy (e.g. nginx `client_max_body_size`), since the app itself doesn't limit total body size.
    *   *Frontend:* `ChallengeCameraCapture.jsx` shows the challenge prompt and captures a 13-frame burst (one every 150ms); `Login.jsx` posts it to `/login/step2` and, on failure, returns to step 1 with the error shown.

## Security fixes in this pass

The original scaffold had two real vulnerabilities, now fixed:

*   **`/login/step2` was a full password bypass.** It issued a token on face match alone, with nothing proving step 1 (the password) had ever succeeded - anyone who knew a username and could pass the face check got in with no password at all. Fixed with the `session_id` binding described above (`backend/models.py`: `VerificationSession`; `backend/services/auth.py`: `create_verification_session` / `consume_verification_session`). Session IDs are single-use, expire after 90s (`MFA_VERIFICATION_TTL_SECONDS`), and are bound to the exact user + device that completed step 1.
*   **`/history/{username}` was unauthenticated** - anyone could read anyone's login history by URL. It's now `GET /history` (no username in the URL), protected by a JWT bearer-token dependency (`auth.get_current_user`) that derives identity from the verified token, so a caller can only ever see their own history.

Both are exercised by an API-level test suite during development (see commit history) covering: no-session-id, forged session-id, wrong-device session-id, session replay, and unauthenticated/invalid-token history access - all correctly rejected with 401.

**Known limitation, by design for a prototype**: the client posts base64 images to the server - there's still no hardware proof they came from a live camera. The randomized blink/head-turn challenge raises the bar over a single static photo, but its replay resistance is limited: there are only 3 possible challenges, and step 1 can be retried without any rate limit, so an attacker who knows the password and holds a pre-recorded clip of even one action can keep restarting at step 1 until that action comes up (1 in 3 per try). The same burst can also be resubmitted against a new session. Rate limiting / lockout on failed step-2 attempts (and optionally rejecting duplicate bursts) is recommended future work. None of this is a substitute for hardware attestation (Android Play Integrity), and the gesture/PAD thresholds are starting defaults that should be calibrated on your own captured data.

## Project Structure

```
adaptive-mfa/
├── backend/
│   ├── main.py                 # FastAPI application and endpoints
│   ├── config.py                # Env-overridable secrets/thresholds (no hardcoded secrets)
│   ├── requirements.txt        # Runtime deps only (no TensorFlow/DeepFace)
│   ├── requirements-dev.txt    # Tests + ONNX export deps (pytest, httpx, TensorFlow, DeepFace, onnx)
│   ├── database.py             # SQLite setup
│   ├── models.py               # SQLAlchemy models (User, Device, LoginHistory, VerificationSession)
│   ├── schemas.py              # Pydantic schemas for requests/responses
│   ├── services/
│   │   ├── auth.py             # Risk calculation, JWT, password hashing, session binding
│   │   ├── biometrics.py       # Step-2 orchestration: detect -> PAD -> gesture -> face match
│   │   ├── face_detect.py      # MediaPipe Face Landmarker: the single detector (bbox + landmarks)
│   │   ├── liveness_challenge.py # Blink / head-turn verification from a landmark sequence
│   │   ├── face_match_onnx.py  # ArcFace alignment, embedding, and matching via ONNX Runtime
│   │   └── pad_onnx.py         # Anti-spoofing inference via ONNX Runtime (no torch, no own detector)
│   ├── anti_spoofing/          # Vendored Silent-Face-Anti-Spoofing source + weights
│   │   ├── export_onnx.py      # One-time .pth -> ONNX conversion script (PAD)
│   │   └── export_arcface_onnx.py # One-time DeepFace ArcFace -> ONNX conversion script
│   ├── models/                 # Served ONNX models + face_landmarker.task (committed - small)
│   │   └── arcface/            # arcface.onnx (~137MB, git-ignored; generate with export_arcface_onnx.py)
│   └── tests/                  # pytest suite; fixtures/README.md explains the optional sample photo
├── ml/                          # Trainable PAD model pipeline (see ml/README.md)
│   ├── model.py  train.py  eval.py  export.py
│   └── data/                   # Dataset manifest schema + adapters (CelebA-Spoof, synthetic smoke test)
└── frontend/
    ├── src/
    │   ├── App.jsx             # React router setup
    │   ├── components/
    │   │   ├── CameraCapture.jsx # Webcam capture component (enrollment photo)
    │   │   └── ChallengeCameraCapture.jsx # Step-2 challenge prompt + burst capture
    │   └── pages/
    │       ├── Enroll.jsx      # Enrollment page
    │       ├── Login.jsx       # Adaptive login page (carries session_id to step 2)
    │       └── Dashboard.jsx   # Post-login dashboard showing history (authenticated)
    └── ...
```

## Setup Instructions

### 1. Backend Setup

Open a terminal and navigate to the `backend` directory:

```bash
cd backend
```

Create a virtual environment and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Note on Dependencies:** `requirements.txt` contains only what the server needs at request time. It no longer includes PyTorch, TensorFlow, or DeepFace: the anti-spoofing and ArcFace models run via `onnxruntime`, and face detection/landmarks via `mediapipe` (smaller install, faster cold start, lower RAM). PyTorch is only needed once, to (re)generate the PAD ONNX files from the vendored `.pth` weights (`backend/anti_spoofing/export_onnx.py`). The PAD ONNX files and `face_landmarker.task` are committed under `backend/models/`.
>
> **The ArcFace ONNX model is not committed** (~137MB, over GitHub's file limit; `backend/models/arcface/` is git-ignored). The app imports without it, but enrollment and step-2 verification need it, so generate it once - see "Export the ArcFace ONNX model" below.
>
> **`mediapipe` is pinned** to `0.10.35` (1.0.x crashes on macOS arm64) and **`opencv-python` is pinned** to `4.11.0.86` (an unpinned install can resolve to a 5.x build missing `cv2.CascadeClassifier`/`cv2.dnn.readNetFromCaffe` in some environments). Both rationales are in `requirements.txt`.

#### Export the ArcFace ONNX model (one-time)

This step uses TensorFlow and DeepFace, which are dev-only dependencies (`backend/requirements-dev.txt`). From `backend/`, with the venv active:

```bash
pip install -r requirements-dev.txt
pip install --no-deps tf2onnx   # deliberately not in requirements-dev.txt, see below
python anti_spoofing/export_arcface_onnx.py
# writes backend/models/arcface/arcface.onnx
```

`tf2onnx` is installed separately with `--no-deps` because its package metadata pins `protobuf~=3.20`, which would downgrade protobuf below what TensorFlow and mediapipe need and break both at import time; its code runs fine on the protobuf version already installed. The first run downloads DeepFace's ArcFace weights (~100MB) before converting them; the script self-checks the ONNX output against the Keras model. This is the same one-time-export pattern as the PAD model's `backend/anti_spoofing/export_onnx.py`.

#### Upgrading an existing database

If your `backend/mfa_demo.db` was created before the blink/head-turn challenge was added, its `verification_sessions` table has no `challenge_type` column. The app's startup `create_all` only creates missing *tables*, it never adds columns to existing ones, so every HIGH-risk `/login/step1` would fail with a 500. Add the column once, from the repo root, with the server stopped:

```bash
sqlite3 backend/mfa_demo.db "ALTER TABLE verification_sessions ADD COLUMN challenge_type VARCHAR;"
```

This keeps all enrolled users, devices and login history. Deleting `mfa_demo.db` also fixes the error (it's recreated on startup), but you lose every enrollment made so far, including any DeepFace-era embeddings that still verify against the ONNX model, and users have to enroll again. A fresh checkout with no `mfa_demo.db` needs neither step.

#### Running the tests

```bash
pip install -r requirements-dev.txt   # if not already done above
python -m pytest tests/ -v
```

Some tests need a real face photo and **skip** unless you add one: save a clear, front-facing, well-lit selfie as `backend/tests/fixtures/sample_face.jpg` (see `backend/tests/fixtures/README.md`). Fixture images are git-ignored (`*.jpg`, `*.jpeg`, `*.png` under `backend/tests/fixtures/`). The ArcFace parity test also needs the exported `arcface.onnx` and skips without it. The `backend/tests/manual_check_*.py` scripts use the same photo for manual inspection.

#### Configuration

Thresholds and limits are overridable via environment variables (see `backend/config.py` for the authoritative list and defaults):

| Variable | Default | Purpose |
| --- | --- | --- |
| `MFA_SECRET_KEY` | generated, persisted to `backend/.secret_key` | JWT signing key |
| `MFA_ACCESS_TOKEN_EXPIRE_MINUTES` | 30 | Access token lifetime |
| `MFA_VERIFICATION_TTL_SECONDS` | 90 | Lifetime of the step-2 `session_id` |
| `MFA_FACE_MATCH_THRESHOLD` | 0.45 | ArcFace cosine-distance threshold (lower is stricter) |
| `MFA_LIVENESS_THRESHOLD` | 0.5 | Per-frame PAD "live" score threshold |
| `MFA_LIVENESS_MIN_LIVE_FRAME_FRACTION` | 0.8 | Fraction of burst frames that must pass PAD |
| `MFA_LIVENESS_MIN_VALID_FRAMES` | 6 | Minimum frames with exactly one detected face, else "retake" |
| `MFA_LIVENESS_MAX_FRAMES` | 30 | Max frames accepted per step-2 request (frontend burst must be <= this) |
| `MFA_BLINK_EAR_THRESHOLD` | 0.2 | Eye-aspect-ratio threshold for the blink challenge |
| `MFA_HEAD_TURN_DISPLACEMENT_THRESHOLD` | 0.15 | Head-turn threshold: change in the nose tip's offset from the eye midpoint, in units of inner-eye distance |
| `MFA_HEAD_TURN_MIN_INTERMEDIATE_FRAMES` | 2 | A head turn must pass through this many in-between frames (15%-85% of the way); blocks a photo followed by its mirror image, or two photos in different poses |
| `MFA_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed origins |
| `MFA_ALWAYS_REQUIRE_FACE_CHECK` | 0 | Testing switch: `1` makes every login require the step-2 face check, even from a known device (risk score is still computed and shown) |

The gesture and burst thresholds are uncalibrated starting defaults; tune them on your own captured data.

Run the FastAPI server (bound to `0.0.0.0` so it's accessible on your local network):

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

> **Mobile Testing Note**: If you plan to test the frontend on a mobile device over the same Wi-Fi network, you'll need to add your dev machine's LAN IP address (e.g. `http://192.168.1.5:5173`) to the `allow_origins` list in `backend/main.py`.

*The server will start on `http://0.0.0.0:8000`. Nothing is downloaded at runtime; `backend/models/arcface/arcface.onnx` must already exist (see the export step above).*

### 2. Frontend Setup

Open a **new** terminal and navigate to the `frontend` directory:

```bash
cd frontend
```

Install the dependencies:

```bash
npm install
```

Start the React development server:

```bash
npm run dev
```

*The frontend will start (usually on `http://localhost:5173`). Check your terminal for the exact local URL.*

## How to Test the Demo

1.  **Enroll**: Go to the frontend URL, click "Enroll", create an account, and capture your face.
2.  **Low Risk Login**: Immediately go to "Login" and log in with the same account. Because it's the same device (simulated via browser `localStorage`), the risk score will be **LOW** and you will bypass the face check.
3.  **High Risk Login**: Open an Incognito/Private window (which clears the `localStorage` device ID) and attempt to log in. The risk score will be **HIGH**, and you will be forced to complete the face verification step.
4.  **Anti-Spoofing Test**: During a High Risk Login, try holding up a photo of yourself on your phone to the webcam. The liveness detection should reject it. (Note: anti-spoofing models can be sensitive to lighting and webcam quality; you may need good lighting for it to recognize a real face).
5.  **Dashboard**: After logging in, you will see a history table showing all attempts, their risk scores, and whether the face check was triggered.
6.  **Security fixes**: try calling `POST /login/step2` directly (e.g. via `/docs`) without first calling `/login/step1`, or with a made-up `session_id` - it should be rejected with 401. Try `GET /history` without an `Authorization: Bearer <token>` header - also 401.

## Training your own anti-spoofing model

The app ships with a pretrained, vendored anti-spoofing model (now served via ONNX Runtime). `ml/` contains a separate, from-scratch-trainable model - MobileNetV3-Small with binary live/spoof + attack-type + auxiliary FFT-artifact heads - built for free data and free compute (Colab's T4 tier). See `ml/README.md` for how to get data, train, evaluate (APCER/BPCER/ACER), export to ONNX, and drop the result into `backend/models/` to replace the vendored model.

## Running on Android

The frontend is a standard React SPA, so the lowest-friction path to an Android build is wrapping it with [Capacitor](https://capacitorjs.com/) rather than writing a second native client:

```bash
cd frontend
npm install @capacitor/core @capacitor/android
npx cap init "Adaptive MFA" "com.example.adaptivemfa" --web-dir=dist
npm run build
npx cap add android
npx cap sync android
npx cap open android   # opens Android Studio
```

Notes:
*   `react-webcam` (already used by `CameraCapture.jsx`) works inside Capacitor's WebView via `getUserMedia`, so no camera-plugin rewrite should be needed for the web camera flow to work as-is.
*   Add the `android.permission.CAMERA` and `android.permission.INTERNET` permissions in `android/app/src/main/AndroidManifest.xml`.
*   Point `API_URL` (currently derived from `window.location.hostname` in `Login.jsx`/`Enroll.jsx`/`Dashboard.jsx`) at your backend's real, reachable address - `localhost` won't resolve from inside the Android WebView/emulator.
*   This has **not** been built or run in this pass - there's no Android SDK/emulator in the environment this was developed in. The steps above are the intended path, not a verified one; the actual native build and camera-permission flow still need to be exercised on a real device or emulator.
*   Once the `ml/` PAD model is trained and exported to ONNX, the same file runs on Android via `onnxruntime-react-native` (or plain `onnxruntime-android` if a fully native client is built later) - no separate mobile-specific conversion needed, since ONNX is the one artifact shared across server, web, and Android.
