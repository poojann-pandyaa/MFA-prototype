"""
Centralized, env-overridable configuration for the prototype.

Nothing here is hardcoded into source control as a real secret: SECRET_KEY
falls back to a value generated on first run and persisted to a gitignored
local file, so tokens survive a dev-server reload but never live in git.
Thresholds are configurable but default to the values already in use so we
don't silently change security behavior that hasn't been measured yet
(see ml/eval.py for how to calibrate them against real data).
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_or_create_secret_key() -> str:
    env_key = os.environ.get("MFA_SECRET_KEY")
    if env_key:
        return env_key

    key_path = BASE_DIR / ".secret_key"
    if key_path.exists():
        existing = key_path.read_text().strip()
        if existing:
            return existing

    generated = secrets.token_hex(32)
    try:
        key_path.write_text(generated)
    except OSError:
        # Read-only filesystem, etc. - fall back to an ephemeral key rather
        # than crashing; tokens just won't survive a restart in that case.
        pass
    return generated


SECRET_KEY = _load_or_create_secret_key()
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("MFA_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# Step-2 verification session (nonce) TTL - how long the user has to
# complete the face check after step 1 before the session expires.
VERIFICATION_SESSION_TTL_SECONDS = int(os.environ.get("MFA_VERIFICATION_TTL_SECONDS", "90"))

# Face-matching cosine-distance threshold (lower = stricter). ArcFace's own
# calibrated threshold is ~0.68; this repo's original code used 0.45 without
# citing a measurement. We keep that same default here (no behavior change)
# but make it overridable so it can be calibrated with ml/eval.py once real
# enrollment/impostor data is available.
FACE_MATCH_THRESHOLD = float(os.environ.get("MFA_FACE_MATCH_THRESHOLD", "0.45"))

# Anti-spoofing "real face" confidence threshold (matches prior behavior).
LIVENESS_THRESHOLD = float(os.environ.get("MFA_LIVENESS_THRESHOLD", "0.8"))

CORS_ALLOW_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "MFA_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]
