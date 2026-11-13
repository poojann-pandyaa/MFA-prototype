import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.db import Base


def now():
    return datetime.datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=now)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    balance = Column(Float)
    number = Column(String, nullable=True)       # set for accounts opened through onboarding; quick demo accounts have none
    ifsc = Column(String, nullable=True)
    kind = Column(String, default="Savings")
    opened_at = Column(DateTime, default=now)


class Profile(Base):
    """Who a pre-registered customer is (all invented). The risk model does not use these fields; the policy uses age (see pay.py)."""
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    full_name = Column(String)
    age = Column(Integer)
    city = Column(String)
    occupation = Column(String)
    mobile = Column(String)
    story = Column(String)


class Directory(Base):
    """A fake bank directory: who owns each payee id, and what the platform knows about the account."""
    __tablename__ = "directory"
    vpa = Column(String, primary_key=True)
    registered_name = Column(String)
    label = Column(String)              # short description for the demo quick-pick
    age_days = Column(Float)            # how old the account is on the platform
    fan_in_24h = Column(Integer)        # distinct other payers in the last 24h
    is_merchant = Column(Boolean, default=False)
    risky_category = Column(Boolean, default=False)
    fri = Column(Integer, nullable=True)         # government fraud-risk band: 0 none, 1 medium, 2 high, 3 very high
    complaints = Column(Integer, default=0)
    similar_to = Column(String, nullable=True)   # demo hint only, never used by the model


class UserAuthMethod(Base):
    __tablename__ = "user_auth_methods"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    method = Column(String)
    active = Column(Boolean, default=True)
    enrolled_at = Column(DateTime, default=now)
    delay_until = Column(DateTime, nullable=True)   # a new method counts only after this time


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    payee_vpa = Column(String, index=True)
    amount = Column(Float)
    channel = Column(String, default="pay")
    note = Column(String, default="")
    device_id = Column(String)
    created_at = Column(DateTime, default=now, index=True)
    # planned -> pending (steps to do) -> approved (ready for PIN) -> completed | blocked | cancelled
    status = Column(String, default="planned")
    severity = Column(String)
    threat = Column(String)
    score = Column(Float)
    plan_json = Column(Text)
    steps_done = Column(Integer, default=0)
    explain_json = Column(Text)      # X-ray; only ever served in demo mode
    message = Column(String, default="")
    completed_at = Column(DateTime, nullable=True)


class AuthChallenge(Base):
    """One run of one authenticator step for one payment (also serves as the step outcome log)."""
    __tablename__ = "auth_challenges"
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), index=True)
    step_index = Column(Integer)
    method = Column(String)
    nonce = Column(String)
    payload_hash = Column(String)    # sha256(payee | amount | nonce): ties the challenge to this payment
    data_json = Column(Text)         # what the client may see
    secret_json = Column(Text)       # what only the server may see
    created_at = Column(DateTime, default=now)
    expires_at = Column(DateTime)
    attempts = Column(Integer, default=0)
    status = Column(String, default="open")   # open | passed | failed | expired
    seconds = Column(Float, nullable=True)
    detail = Column(String, default="")       # outcome note (counts-level, no private data)
