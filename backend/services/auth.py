import bcrypt
import jwt
import datetime
import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
from models import User, Device, LoginHistory, VerificationSession
import config

SECRET_KEY = config.SECRET_KEY
ALGORITHM = config.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = config.ACCESS_TOKEN_EXPIRE_MINUTES

_bearer_scheme = HTTPBearer(auto_error=False)

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password=pwd_bytes, salt=salt)
    return hashed_password.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_byte_enc = plain_password.encode('utf-8')
    hashed_password_byte_enc = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password=password_byte_enc, hashed_password=hashed_password_byte_enc)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def calculate_risk_score(db: Session, user: User, device_identifier: str) -> str:
    # Check if this device has been used by this user before
    device = db.query(Device).filter(Device.user_id == user.id, Device.device_identifier == device_identifier).first()
    
    if not device:
        return "HIGH" # New device -> High risk

    # For a demo, we can just say if it's a known device, it's LOW risk.
    # To add a bit more "adaptive" feel, we could check the hour of the day
    # compared to previous logins.
    
    history = db.query(LoginHistory).filter(LoginHistory.user_id == user.id, LoginHistory.success == True).all()
    if len(history) < 3:
        # Not enough history to judge time of day, rely on device
        return "LOW"

    # Simple time anomaly: find average login hour
    hours = [h.timestamp.hour for h in history]
    avg_hour = sum(hours) / len(hours)
    current_hour = datetime.datetime.utcnow().hour
    
    # If login is more than 4 hours away from average, maybe medium risk
    if abs(current_hour - avg_hour) > 4 and abs(current_hour - avg_hour) < 20: # handle wrapping 24h roughly
        return "MEDIUM"

    return "LOW"


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


def consume_verification_session(db: Session, session_id: str, user: User, device_identifier: str) -> VerificationSession:
    """
    Validates and atomically consumes a verification session for step 2.
    Raises HTTPException (401) if missing, expired, already used, or bound
    to a different user/device than the one presenting it.
    """
    vs = db.query(VerificationSession).filter(VerificationSession.session_id == session_id).first()
    if (
        not vs
        or vs.user_id != user.id
        or vs.device_identifier != device_identifier
        or vs.consumed
        or vs.expires_at < datetime.datetime.utcnow()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or already-used verification session. Please log in again.",
        )
    vs.consumed = True
    db.commit()
    return vs


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: decodes the bearer JWT and loads the user, or 401s."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
