import bcrypt
import jwt
import datetime
from sqlalchemy.orm import Session
from models import User, Device, LoginHistory
from schemas import UserCreate

SECRET_KEY = "supersecretkey_for_demo_only"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

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
