"""DemoPay settings. Everything is overridable by environment variable; defaults suit the demo."""
import os
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


# Demo mode: enables the model X-ray, persona loading and signal toggles. Turn OFF in any real deployment:
# the X-ray exposes the reasons behind a decision, which would help an attacker.
DEMO = _flag("DEMOPAY_DEMO", "1")
DB_URL = os.environ.get("DEMOPAY_DB", f"sqlite:///{BASE / 'demopay.db'}")


def _secret_key() -> str:
    if os.environ.get("DEMOPAY_SECRET_KEY"):
        return os.environ["DEMOPAY_SECRET_KEY"]
    path = BASE / ".secret_key"
    if path.exists() and path.read_text().strip():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    try:
        path.write_text(key)
    except OSError:
        pass
    return key


SECRET_KEY = _secret_key()
JWT_HOURS = 8
OPENING_BALANCE = 100_000.0

# How common scams are among the payments this app sees. The risk model only says how unusual a payment is; this
# number turns that into a probability. Real UPI is reported at about 1 in 100,000 (1e-5). The demo default is higher
# (1 in 10,000, like a high-risk channel) so the engine visibly acts; the X-ray shows the value in use. At 1e-5 most
# alerts are genuine users and the engine mostly allows; at 1e-3 it blocks sooner.
BASE_RATE = float(os.environ.get("DEMOPAY_BASE_RATE", "1e-4"))

# Cooling-off hold. Real value 30 minutes; the demo shortens it and says so on screen.
HOLD_SECONDS = int(os.environ.get("DEMOPAY_HOLD_SECONDS", "20" if DEMO else "1800"))
REAL_HOLD_SECONDS = 1800

# Friction rules for the decision engine (user seconds spent on fast steps). None = no limit.
TIME_BUDGET = {"MEDIUM": 10.0, "HIGH": 30.0, "CRITICAL": None}
SLOW_AMOUNT = float(os.environ.get("DEMOPAY_SLOW_AMOUNT", "50000"))  # HIGH + tricked-owner + at least this amount may use slow steps
CHALLENGE_TTL_SECONDS = 600
MAX_ATTEMPTS = 3
MIN_READ_SECONDS = float(os.environ.get("DEMOPAY_MIN_READ_SECONDS", "4"))

# Customers aged SENIOR_AGE or more get slow checks from a lower amount. ASSUMPTION (older customers are more often coached), not measured.
SENIOR_AGE = 60
SENIOR_SLOW_AMOUNT = SLOW_AMOUNT / 5
DEMO_PASSWORD = "demo1234"     # every pre-registered demo user signs in with this

CORS_ORIGINS = [o.strip() for o in os.environ.get("DEMOPAY_CORS", "http://localhost:5180,http://127.0.0.1:5180").split(",") if o.strip()]
