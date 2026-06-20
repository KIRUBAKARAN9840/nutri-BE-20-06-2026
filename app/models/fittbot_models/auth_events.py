from sqlalchemy import Column, Integer, String, DateTime, Index
from app.models.database import Base
from datetime import datetime


class AuthEvent(Base):
    """Persistent audit trail for all authentication events.

    Every OTP request, verification, login, registration, and token
    refresh is recorded here for forensics and analytics.
    """
    __tablename__ = "auth_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=True, index=True)
    mobile = Column(String(15), nullable=True, index=True)
    event_type = Column(String(50), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(512), nullable=True)
    status = Column(String(20), nullable=False, default="success")
    detail = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_auth_events_mobile_type", "mobile", "event_type"),
    )
