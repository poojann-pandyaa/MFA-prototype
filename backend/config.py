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

# Anti-spoofing "real face" confidence threshold. Calibrated against the
# ml/-trained custom PAD model's score distribution on its LCC-FASD test
# split (see ml/eval.py output): 0.5 gives APCER=8.7% / BPCER=12.7% there.
# The prior 0.8 was calibrated for the vendored MiniFASNet ensemble this
# model replaces and doesn't carry over - re-run ml/eval.py and adjust
# this if real-world acceptance/rejection rates diverge from those numbers.
LIVENESS_THRESHOLD = float(os.environ.get("MFA_LIVENESS_THRESHOLD", "0.5"))

# Step-2 challenge-response actions. Randomized per verification session
# so a pre-recorded video of the legitimate user can't anticipate which
# one will be asked for - see liveness_challenge.py for how each is
# verified from the landmark sequence.
CHALLENGE_TYPES = ["blink", "turn_left", "turn_right"]

# Testing switch: when on, every login needs the step-2 face check, even from
# a known device at a normal hour (the risk score is still computed and shown,
# it just no longer lets LOW skip step 2). Off by default - normal adaptive
# behavior. Enable with MFA_ALWAYS_REQUIRE_FACE_CHECK=1.
ALWAYS_REQUIRE_FACE_CHECK = os.environ.get("MFA_ALWAYS_REQUIRE_FACE_CHECK", "0").lower() in ("1", "true", "yes")

# Gesture-verification thresholds (services/liveness_challenge.py). These
# are starting defaults, not measured values - calibrate against your own
# captured burst data (see tests/fixtures/README.md) the same way
# LIVENESS_THRESHOLD above was calibrated against ml/eval.py's output.
BLINK_EAR_THRESHOLD = float(os.environ.get("MFA_BLINK_EAR_THRESHOLD", "0.2"))
HEAD_TURN_DISPLACEMENT_THRESHOLD = float(os.environ.get("MFA_HEAD_TURN_DISPLACEMENT_THRESHOLD", "0.15"))
# A turn must pass through the poses in between: at least this many frames
# must sit partway (15%-85%) between the start and end pose. Stops a photo
# followed by its mirror image, or two photos in different poses, which jump
# from one pose to the other with nothing in between. At the frontend's 150ms
# interval, 2 means the turn must take at least ~0.45s (3 frame steps).
HEAD_TURN_MIN_INTERMEDIATE_FRAMES = int(os.environ.get("MFA_HEAD_TURN_MIN_INTERMEDIATE_FRAMES", "2"))

# Step-2 capture burst (frontend captures this many frames; see Task 11's
# ChallengeCameraCapture.jsx). Starting defaults, not measured values.
LIVENESS_MIN_VALID_FRAMES = int(os.environ.get("MFA_LIVENESS_MIN_VALID_FRAMES", "6"))
LIVENESS_MIN_LIVE_FRAME_FRACTION = float(os.environ.get("MFA_LIVENESS_MIN_LIVE_FRAME_FRACTION", "0.8"))
# Upper bound on frames accepted per step-2 request; /login/step2 rejects
# larger bursts before decoding anything, so a client can't make the server
# decode/run ML on an unbounded number of images.
LIVENESS_MAX_FRAMES = int(os.environ.get("MFA_LIVENESS_MAX_FRAMES", "30"))

CORS_ALLOW_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "MFA_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]
