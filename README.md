# Adaptive MFA Demo

This is a working demo of an Adaptive Multi-Factor Authentication (MFA) web application using face recognition and liveness detection.

## Architecture

*   **Backend**: Python, FastAPI, SQLite
*   **Frontend**: React (Vite, TailwindCSS)
*   **Biometrics**:
    *   Face Matching: DeepFace (ArcFace model) - pretrained, not trained in this repo (see `ml/README.md` for why)
    *   Liveness Detection (anti-spoofing): a MobileNetV3-Small model served via **ONNX Runtime** (`backend/services/pad_onnx.py`). Ships today with the vendored, pretrained minivision-ai/Silent-Face-Anti-Spoofing (MiniFASNet) weights converted to ONNX (`backend/anti_spoofing/export_onnx.py`); `ml/` contains a from-scratch **trainable** replacement for this model (multi-task: binary live/spoof + attack type + an auxiliary FFT-artifact head) that you can train on free data and drop in to replace it - see `ml/README.md`.

## Authentication Flow

1.  **Enrollment**: The user provides a username, password, and captures a face photo via their webcam. The backend extracts the face embedding (a mathematical representation of the face) using DeepFace and stores only the embedding and a hashed password in the SQLite database.
2.  **Login Step 1**: The user enters their username and password. If correct, the backend calculates a risk score and, if step 2 is required, issues a short-lived, single-use `session_id` bound to that user+device.
    *   If it's a known device and the login time is within normal bounds, the risk score is **LOW**, and the user is logged in directly.
    *   If it's a new device or unusual time, the risk score is **HIGH** (or MEDIUM), and Step 2 is triggered.
3.  **Login Step 2 (Adaptive)**: The user is prompted to capture a live face photo. The request must include the `session_id` from step 1 - without a valid, unexpired, not-yet-used session bound to the same user and device, the server rejects the request before even looking at the image. This is what stops step 2 from being callable as a bypass of the password step (see "Security fixes" below).
    *   **Liveness Check**: The photo is passed through the anti-spoofing model (ONNX Runtime). If the face is detected as a spoof (e.g., a photo or screen replay), access is denied.
    *   **Face Matching**: If liveness passes, DeepFace compares the captured face against the stored enrollment embedding. If they match, the user is logged in.

## Security fixes in this pass

The original scaffold had two real vulnerabilities, now fixed:

*   **`/login/step2` was a full password bypass.** It issued a token on face match alone, with nothing proving step 1 (the password) had ever succeeded - anyone who knew a username and could pass the face check got in with no password at all. Fixed with the `session_id` binding described above (`backend/models.py`: `VerificationSession`; `backend/services/auth.py`: `create_verification_session` / `consume_verification_session`). Session IDs are single-use, expire after 90s (`MFA_VERIFICATION_TTL_SECONDS`), and are bound to the exact user + device that completed step 1.
*   **`/history/{username}` was unauthenticated** - anyone could read anyone's login history by URL. It's now `GET /history` (no username in the URL), protected by a JWT bearer-token dependency (`auth.get_current_user`) that derives identity from the verified token, so a caller can only ever see their own history.

Both are exercised by an API-level test suite during development (see commit history) covering: no-session-id, forged session-id, wrong-device session-id, session replay, and unauthenticated/invalid-token history access - all correctly rejected with 401.

**Known limitation, by design for a prototype**: the client posts a base64 image to the server - there's no proof it came from a live camera versus a file upload. An active challenge-response (blink/head-turn prompt) or hardware attestation (Android Play Integrity) would close this, but that's flagged as future work rather than built here, to keep this a prototype rather than a production hardening pass.

## Project Structure

```
adaptive-mfa/
├── backend/
│   ├── main.py                 # FastAPI application and endpoints
│   ├── config.py                # Env-overridable secrets/thresholds (no hardcoded secrets)
│   ├── database.py             # SQLite setup
│   ├── models.py               # SQLAlchemy models (User, Device, LoginHistory, VerificationSession)
│   ├── schemas.py              # Pydantic schemas for requests/responses
│   ├── services/
│   │   ├── auth.py             # Risk calculation, JWT, password hashing, session binding
│   │   ├── biometrics.py       # DeepFace matching + PAD orchestration
│   │   └── pad_onnx.py         # Anti-spoofing inference via ONNX Runtime (no torch at request time)
│   ├── anti_spoofing/          # Vendored Silent-Face-Anti-Spoofing source + weights
│   │   └── export_onnx.py      # One-time .pth -> ONNX conversion script
│   └── models/                 # Served ONNX models (committed - small, ~2MB total)
├── ml/                          # Trainable PAD model pipeline (see ml/README.md)
│   ├── model.py  train.py  eval.py  export.py
│   └── data/                   # Dataset manifest schema + adapters (CelebA-Spoof, synthetic smoke test)
└── frontend/
    ├── src/
    │   ├── App.jsx             # React router setup
    │   ├── components/
    │   │   └── CameraCapture.jsx # Webcam capture component
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

> **Note on Dependencies:** `requirements.txt` no longer includes PyTorch - the anti-spoofing model now runs via `onnxruntime` (smaller install, faster cold start, lower RAM). PyTorch is only needed once, to (re)generate the ONNX files from the vendored `.pth` weights; see `backend/anti_spoofing/export_onnx.py`. Pre-converted ONNX files are already committed under `backend/models/`, so a normal `pip install -r requirements.txt` is enough to run the app.
>
> **`opencv-python` is pinned** to `4.11.0.86` in `requirements.txt`. An unpinned install can resolve to a 5.x build that's missing `cv2.CascadeClassifier`/`cv2.dnn.readNetFromCaffe` in some environments, which silently breaks both DeepFace's face detector and this project's own RetinaFace bbox detector.

Run the FastAPI server (bound to `0.0.0.0` so it's accessible on your local network):

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

> **Mobile Testing Note**: If you plan to test the frontend on a mobile device over the same Wi-Fi network, you'll need to add your dev machine's LAN IP address (e.g. `http://192.168.1.5:5173`) to the `allow_origins` list in `backend/main.py`.

*The server will start on `http://0.0.0.0:8000`. On the very first run, DeepFace may download the ArcFace model weights (around 100MB).*

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
