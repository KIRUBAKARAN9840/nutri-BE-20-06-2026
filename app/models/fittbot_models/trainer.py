from sqlalchemy import (
    Column, Integer, UniqueConstraint, String, Float, Text, DateTime,
    ForeignKey, Date, Boolean, JSON, Index,
)
from sqlalchemy.orm import relationship
from app.models.database import Base
from datetime import datetime


class Trainer(Base):
    __tablename__ = "trainers"

    trainer_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    full_name = Column(String(100), nullable=False)
    gender = Column(String(20), nullable=False)
    contact = Column(String(15),unique=True, nullable=False)
    email = Column(String(100), nullable=False)
    specializations = Column(JSON, nullable=True)  # Changed from specialization to specializations as JSON array
    experience = Column(Float, nullable=False)
    certifications = Column(Text, nullable=True)
    work_timings = Column(JSON, nullable=True)  # Changed from availability to work_timings as JSON array
    profile_image = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    password = Column(String(255), nullable=False)
    refresh_token = Column(String(255), nullable=True)

    profiles = relationship("TrainerProfile", back_populates="trainer")


class TrainerProfile(Base):
    __tablename__ = "trainer_profiles"
    profile_id = Column(Integer, primary_key=True, autoincrement=True)
    trainer_id = Column(Integer, ForeignKey("trainers.trainer_id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    full_name = Column(String(100), nullable=False)
    email = Column(String(100), nullable=True)
    specializations = Column(JSON, nullable=True)  # Changed from specialization to specializations as JSON array
    experience = Column(Float, nullable=True)
    certifications = Column(Text, nullable=True)
    work_timings = Column(JSON, nullable=True)  # Changed from availability to work_timings as JSON array
    profile_image = Column(String(255), nullable=True)
    can_view_client_data = Column(Boolean, default=False)
    personal_trainer = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (UniqueConstraint("trainer_id", "gym_id", name="uq_trainer_gym"),)
    trainer = relationship("Trainer", back_populates="profiles")
    gym = relationship("Gym", back_populates="trainer_profiles")


class TrainerAttendance(Base):
    __tablename__ = "trainer_attendance"

    attendance_id = Column(Integer, primary_key=True, autoincrement=True)
    trainer_id = Column(Integer, ForeignKey("trainers.trainer_id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    punch_sessions = Column(JSON, nullable=True)
    total_hours = Column(Float, default=0.0)
    status = Column(String(20), default="active")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("trainer_id", "gym_id", "date", name="uq_trainer_gym_date"),
        Index("idx_trainer_date", "trainer_id", "date"),
        Index("idx_gym_date", "gym_id", "date"),
    )

    trainer = relationship("Trainer")
    gym = relationship("Gym")
