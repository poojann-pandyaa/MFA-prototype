import datetime

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import User

_bearer = HTTPBearer(auto_error=False)


def make_token(user_id: int) -> str:
    exp = datetime.datetime.utcnow() + datetime.timedelta(hours=config.JWT_HOURS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, config.SECRET_KEY, algorithm="HS256")


def current_user(cred: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        uid = int(jwt.decode(cred.credentials, config.SECRET_KEY, algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return user


def device_id(x_device_id: str = Header(default="web-device")) -> str:
    return x_device_id[:64]


def demo_only():
    if not config.DEMO:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
