from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
import datetime
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    face_embedding = Column(String, nullable=True) # Store JSON string of the embedding vector

    devices = relationship("Device", back_populates="user")
    login_history = relationship("LoginHistory", back_populates="user")

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    device_identifier = Column(String, index=True)

    user = relationship("User", back_populates="devices")

class LoginHistory(Base):
    __tablename__ = "login_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    device_identifier = Column(String)
    risk_score = Column(String) # "LOW", "MEDIUM", "HIGH"
    face_check_triggered = Column(Boolean, default=False)
    success = Column(Boolean, default=False)

    user = relationship("User", back_populates="login_history")
