from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Date, Boolean,
)
from app.models.database import Base
from datetime import datetime


class Gym_Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    tag=Column(String(100),nullable=False)
    ratings=Column(Integer,nullable=False)
    feedback = Column(Text, nullable=True)
    timing = Column(DateTime, default=datetime.now)


class Feedback(Base):
    __tablename__ = "ffeedback"

    id = Column(Integer, primary_key=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    tag=Column(String(100),nullable=False)
    ratings=Column(Integer,nullable=False)
    feedback = Column(Text, nullable=True)
    timing = Column(DateTime, default=datetime.now)


class ClientToken(Base):
    __tablename__ = "support_tokens"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    client_id  = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    token      = Column(String(255), nullable=False)
    subject    = Column(String(255))
    email      = Column(String(255))
    issue      = Column(Text)
    followed_up  = Column(Boolean, nullable=False, default=False)
    resolved  = Column(Boolean, nullable=False, default=False)
    comments = Column(Text)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    resolved_at = Column(DateTime(timezone=True))


class OwnerToken(Base):
    __tablename__ = "support_tokens_owner"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    gym_id  = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    token      = Column(String(255), nullable=False)
    subject    = Column(String(255))
    email      = Column(String(255))
    issue      = Column(Text)
    followed_up  = Column(Boolean, nullable=False, default=False)
    resolved  = Column(Boolean, nullable=False, default=False)
    comments = Column(Text)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class ClientFeedback(Base):
    __tablename__ = "client_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, canceled, submitted
    next_feedback_date = Column(Date, nullable=True)  # When to ask next if canceled
    feedback_text = Column(Text, nullable=True)  # The actual feedback if submitted
    rating = Column(Integer, nullable=True)  # Optional rating (1-5)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class FittbotRatings(Base):
    __tablename__ = "fittbot_ratings"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    star = Column(Integer, nullable=False)
    feedback = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class FreeTrial(Base):
    __tablename__ = "free_trial"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.now)


class DeleteRequest(Base):
    """Delete account requests from clients"""
    __tablename__ = "delete_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    feedback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class OwnerDeleteRequest(Base):
    """Delete account requests from owners (Fittbot Business)"""
    __tablename__ = "owner_delete_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, index=True)
    feedback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
