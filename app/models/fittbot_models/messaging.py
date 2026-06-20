from sqlalchemy import (
    Column, Integer, String, Float, Enum, Text, DateTime, ForeignKey, Date,
    Time, Boolean, JSON,
)
from app.models.database import Base
from datetime import datetime


class Message(Base):
    __tablename__ = "messages"

    message_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    sender_id = Column(Integer, nullable=False)
    recipient_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=False)
    sender_role = Column(Enum("owner", "trainer", "client"), nullable=False)
    recipient_role = Column(Enum("owner", "trainer", "client"), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.now)
    is_read = Column(Boolean,default=False)


class Notification(Base):
    __tablename__ = "notifications"

    notification_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    role = Column(Enum("owner", "trainer", "client"), nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)


class FcmToken(Base):
    __tablename__ = "fcm_tokens"

    token_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=False)
    fcm_token = Column(String(512), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class Reminder(Base):
    __tablename__ = "reminders"

    reminder_id = Column(Integer, primary_key=True, index=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True)
    gym_id = Column(Integer, nullable=True, index=True)
    reminder_time = Column(Time)
    details = Column(String(500), nullable=False)
    vibration_pattern = Column(JSON, nullable=True)
    reminder_type = Column(String(45))
    is_recurring = Column(Boolean, nullable=False, default=False)
    reminder_Sent=Column(Boolean, nullable=False, default=False)
    queued = Column(Boolean, default=False, nullable=False)
    sent_at = Column(DateTime)
    reminder_mode=Column(String(45))
    intimation_start_time=Column(Time)
    intimation_end_time=Column(Time)
    water_timing=Column(Float)
    water_amount=Column(Integer)
    gym_count=Column(Integer)
    diet_type=Column(String(45))
    title=Column(String(45))
    others_time=Column(DateTime)


class GBMessage(Base):
    __tablename__ = "gb_message"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey("gb_sessions.session_id", ondelete="CASCADE"), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.now)


class New_Session(Base):
    __tablename__ = "gb_sessions"
    session_id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, autoincrement=True)
    workout_type = Column(JSON, nullable=False)
    session_time = Column(Time, nullable=False)
    session_date = Column(Date, nullable=False)
    host_id = Column(Integer, ForeignKey("clients.client_id"), nullable=False)
    participant_limit = Column(Integer, nullable=False)
    gender_preference = Column(String(20), nullable=False)


class Participant(Base):
    __tablename__ = "gb_participants"
    participant_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("gb_sessions.session_id"), nullable=False)
    user_id = Column(Integer, ForeignKey("clients.client_id"), nullable=False)
    proposed_time=Column(Time, nullable=False)


class JoinProposal(Base):
    __tablename__ = "gb_join_proposals"
    proposal_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("gb_sessions.session_id"), nullable=False)
    proposer_id = Column(Integer, ForeignKey("clients.client_id"), nullable=False)
    proposed_time=Column(Time, nullable=False)


class RejectedProposal(Base):
    __tablename__ = "gb_rejected_proposals"
    rejected_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("gb_sessions.session_id"), nullable=False)
    user_id = Column(Integer, ForeignKey("clients.client_id"), nullable=False)
