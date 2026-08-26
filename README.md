# Adaptive MFA Demo

This is a working demo of an Adaptive Multi-Factor Authentication (MFA) web application using face recognition and liveness detection.

## Architecture

*   **Backend**: Python, FastAPI, SQLite
*   **Frontend**: React (Vite, TailwindCSS)
*   **Biometrics**:
    *   Face Matching: DeepFace (ArcFace model)
    *   Liveness Detection: minivision-ai/Silent-Face-Anti-Spoofing

## Authentication Flow

1.  **Enrollment**: The user provides a username, password, and captures a face photo via their webcam. The backend extracts the face embedding (a mathematical representation of the face) using DeepFace and stores only the embedding and a hashed password in the SQLite database.
2.  **Login Step 1**: The user enters their username and password. If correct, the backend calculates a risk score.
    *   If it's a known device and the login time is within normal bounds, the risk score is **LOW**, and the user is logged in directly.
    *   If it's a new device or unusual time, the risk score is **HIGH** (or MEDIUM), and Step 2 is triggered.
3.  **Login Step 2 (Adaptive)**: The user is prompted to capture a live face photo.
    *   **Liveness Check**: The photo is first passed through the Silent-Face-Anti-Spoofing model. If the face is detected as a spoof (e.g., a photo on a phone screen), access is denied.
    *   **Face Matching**: If liveness passes, DeepFace compares the captured face against the stored enrollment embedding. If they match, the user is logged in.

## Project Structure

```
adaptive-mfa/
├── backend/
│   ├── main.py                 # FastAPI application and endpoints
│   ├── database.py             # SQLite setup
│   ├── models.py               # SQLAlchemy models (User, Device, LoginHistory)
│   ├── schemas.py              # Pydantic schemas for requests/responses
│   ├── services/
│   │   ├── auth.py             # Risk calculation, JWT, password hashing
│   │   └── biometrics.py       # DeepFace and Anti-Spoofing integration
│   └── anti_spoofing/          # Inference code from Silent-Face-Anti-Spoofing
└── frontend/
    ├── src/
    │   ├── App.jsx             # React router setup
    │   ├── components/
    │   │   └── CameraCapture.jsx # Webcam capture component
    │   └── pages/
    │       ├── Enroll.jsx      # Enrollment page
    │       ├── Login.jsx       # Adaptive login page
    │       └── Dashboard.jsx   # Post-login dashboard showing history
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

> **Note on Dependencies:** The `requirements.txt` includes standard PyTorch and OpenCV which are quite large. The Silent-Face-Anti-Spoofing pre-trained models (`.pth` files) are included inside `backend/anti_spoofing/resources/anti_spoof_models/` as they were cloned during scaffolding.

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
