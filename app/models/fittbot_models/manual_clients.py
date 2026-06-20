from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, ForeignKey, Date, Time,
    Boolean, Index,
)
from app.models.database import Base
from datetime import datetime


class ManualClient(Base):
    __tablename__ = "manual_clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)

    # Personal Info
    name = Column(String(100), nullable=False)
    contact = Column(String(15), nullable=False, index=True)  # Primary identifier
    email = Column(String(100), nullable=True)
    gender = Column(String(20), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    age = Column(Integer, nullable=True)

    # Physical Metrics (optional for manual entry)
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    bmi = Column(Float, nullable=True)
    goal = Column(String(50), nullable=True)  # weight_gain, weight_loss, body_recomposition

    # Membership Info
    admission_number = Column(String(100), nullable=True)  # Owner's custom ID
    batch_id = Column(Integer, nullable=True)
    plan_id = Column(Integer, nullable=True)  # References GymPlans

    # Dates
    joined_at = Column(Date, nullable=True)
    expires_at = Column(Date, nullable=True)

    # Fees
    admission_fee = Column(Float, default=0)
    monthly_fee = Column(Float, default=0)
    total_paid = Column(Float, default=0)
    balance_due = Column(Float, default=0)
    last_payment_date = Column(Date, nullable=True)

    # Status
    status = Column(String(20), default="active")  # active, inactive, expired

    # Notes
    notes = Column(Text, nullable=True)  # Owner can add custom notes

    # Metadata
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Entry tracking
    entry_type = Column(String(20), default="manual")  # Always "manual"

    # Profile Photo
    dp = Column(String(500), nullable=True)  # S3 URL for client photo

    # Indexes for common queries
    __table_args__ = (
        Index("ix_manual_clients_gym_contact", "gym_id", "contact"),
        Index("ix_manual_clients_gym_status", "gym_id", "status"),
    )


class ManualAttendance(Base):
    """Attendance tracking for manual clients - owner punches them in/out"""
    __tablename__ = "manual_attendance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    manual_client_id = Column(Integer, ForeignKey("manual_clients.id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    in_time = Column(Time, nullable=True)
    out_time = Column(Time, nullable=True)
    in_time_2 = Column(Time, nullable=True)
    out_time_2 = Column(Time, nullable=True)
    in_time_3 = Column(Time, nullable=True)
    out_time_3 = Column(Time, nullable=True)
    punched_by = Column(String(20), default="owner")  # Always owner for manual

    __table_args__ = (
        Index("ix_manual_attendance_client_date", "manual_client_id", "date"),
        Index("ix_manual_attendance_gym_date", "gym_id", "date"),
    )


class ManualFeeHistory(Base):
    """Fee payment history for manual clients"""
    __tablename__ = "manual_fee_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    manual_client_id = Column(Integer, ForeignKey("manual_clients.id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    amount = Column(Float, nullable=False)
    payment_method = Column(String(50), nullable=True)
    payment_reference = Column(String(100), nullable=True)
    payment_date = Column(Date, default=lambda: datetime.now().date())
    type = Column(String(20), nullable=True)  # admission, monthly, penalty
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_manual_fee_history_client", "manual_client_id"),
        Index("ix_manual_fee_history_gym_date", "gym_id", "payment_date"),
    )


class ImportClientAttendance(Base):
    """Attendance tracking for imported clients - owner punches them in/out"""
    __tablename__ = "import_client_attendance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_client_id = Column(Integer, ForeignKey("gym_import_data.import_id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    in_time = Column(Time, nullable=True)
    out_time = Column(Time, nullable=True)
    in_time_2 = Column(Time, nullable=True)
    out_time_2 = Column(Time, nullable=True)
    in_time_3 = Column(Time, nullable=True)
    out_time_3 = Column(Time, nullable=True)
    punched_by = Column(String(20), default="owner")

    __table_args__ = (
        Index("ix_import_attendance_client_date", "import_client_id", "date"),
        Index("ix_import_attendance_gym_date", "gym_id", "date"),
    )
