# Project Summary: Adaptive MFA Demo

## What are we doing?
We are demonstrating and running an **Adaptive Multi-Factor Authentication (MFA)** web application. The application intelligently adjusts its security requirements based on user behavior (contextual risk). 
- If a user logs in from a known device, the risk is **low**, and they are logged in directly with a password.
- If they log in from a new or unrecognized device, the risk is **high**, triggering a secondary authentication step that requires a live face scan (biometric verification) with anti-spoofing checks to ensure a real person is present.

## What are we using?

### Core Technologies
- **Frontend**: React (built with Vite) and TailwindCSS for the user interface and webcam capture.
- **Backend**: Python and FastAPI to handle authentication logic, risk scoring, and biometric verification.
- **Database**: SQLite with SQLAlchemy for storing users, hashed passwords, face embeddings, and login history.

### Biometrics & AI Models
- **Face Matching**: [DeepFace](https://github.com/serengil/deepface) (using the ArcFace model) to extract facial embeddings and match a live photo against an enrolled user's face.
- **Liveness Detection**: [Silent-Face-Anti-Spoofing](https://github.com/minivision-ai/Silent-Face-Anti-Spoofing) to detect if a provided image is a real person or a spoof (e.g., a photo held up on a phone screen).
