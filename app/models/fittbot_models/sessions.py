from sqlalchemy import (
    Column, Integer, String, Float, Enum, Text, DateTime, ForeignKey, Date,
    Time, Boolean, JSON, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from app.models.database import Base
from datetime import datetime

SESSION_SCHEMA = "sessions"


class ClassSession(Base):

    __tablename__ = "all_sessions"
    __table_args__ = (UniqueConstraint("name", name="uq_sessions_name"), {"schema": SESSION_SCHEMA})

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), nullable=False)
    image = Column(String(255), nullable=True)
    description = Column(String(255), nullable=False)
    timing = Column(String(50), nullable=False, default="60 Min Session")
    internal= Column(String(45), nullable=True)


class GymSession(Base):
    """
    Map a gym to its available sessions as a JSON blob.
    """
    __tablename__ = "gym_session"
    __table_args__ = {"schema": SESSION_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer)
    sessions = Column(JSON, nullable=False)


class SessionSetting(Base):

    __tablename__ = "session_settings"
    __table_args__ = (
        UniqueConstraint("gym_id", "session_id", "trainer_id", name="uq_session_settings_gym_session_trainer"),
        {"schema": SESSION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.all_sessions.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    trainer_id = Column(Integer, ForeignKey("trainers.trainer_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True)
    is_enabled = Column(Boolean, default=False, nullable=False)
    base_price = Column(Integer, nullable=True)
    discount_percent = Column(Float, default=0.0)
    final_price = Column(Integer, nullable=True)
    capacity = Column(Integer, nullable=True)
    booking_lead_minutes = Column(Integer, nullable=True)
    cancellation_cutoff_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SessionSchedule(Base):
    """
    Recurring/one-off schedules for sessions.
    """
    __tablename__ = "session_schedules"
    __table_args__ = (
        Index("ix_session_schedule_gym_session_trainer", "gym_id", "session_id", "trainer_id"),
        Index("ix_session_schedule_weekday", "weekday", "is_active"),
        {"schema": SESSION_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.all_sessions.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    trainer_id = Column(Integer, ForeignKey("trainers.trainer_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True)
    recurrence = Column(Enum("weekly", "one_off", name="session_recurrence"), default="weekly", nullable=False)
    weekday = Column(Integer, nullable=True)  # 0=Monday .. 6=Sunday for weekly recurrence
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    slot_quota = Column(Integer, nullable=True)  # override capacity per slot
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SessionBooking(Base):
    """
    Bookings across all session types (including personal training).
    """
    __tablename__ = "session_bookings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    session_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.all_sessions.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    trainer_id = Column(Integer, ForeignKey("trainers.trainer_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True)
    schedule_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.session_schedules.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    booking_date = Column(Date, nullable=False)
    status = Column(Enum("booked", "cancelled", "attended", "no_show", "refunded", name="session_booking_status"), default="booked", nullable=False)
    price_paid = Column(Integer, nullable=True)
    discount_applied = Column(Float, nullable=True)
    checkin_token = Column(String(64), unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("ix_session_booking_schedule_date", "schedule_id", "booking_date"),
        Index("ix_session_booking_gym_session", "gym_id", "session_id"),
        {"schema": SESSION_SCHEMA},
    )


class SessionPurchase(Base):
    """
    Session payment/purchase envelope (maps to payment orders/order_items).
    """
    __tablename__ = "session_purchases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    razorpay_order_id = Column(String(64), unique=True, nullable=False)
    payment_order_pk = Column(Integer, nullable=True)
    gym_id = Column(Integer, nullable=False)
    client_id = Column(Integer, nullable=False)
    session_id = Column(Integer, nullable=False)
    trainer_id = Column(Integer, nullable=True)
    sessions_count = Column(Integer, nullable=False)
    scheduled_sessions = Column(JSON, nullable=False)
    reward_applied = Column(Boolean, default=False, nullable=False)
    reward_amount = Column(Integer, default=0, nullable=False)
    total_rupees = Column(Integer, nullable=False)
    payable_rupees = Column(Integer, nullable=False)
    price_per_session = Column(Integer, nullable=True)  # Original per-session price (99 for promo, actual otherwise)
    idempotency_key = Column(String(64), nullable=True)
    status = Column(
        Enum("pending", "paid", "failed", "cancelled", "refunded", name="session_purchase_status"),
        default="pending",
        nullable=False,
    )
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("gym_id", "client_id", "session_id", "trainer_id", "idempotency_key", name="uq_session_purchase_idem"),
        {"schema": SESSION_SCHEMA},
    )


class SessionBookingDay(Base):
    """
    Per-day/slot booking instances tied to a purchase (used for scanning/attendances).
    """
    __tablename__ = "session_booking_days"

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.session_purchases.id", ondelete="CASCADE"), nullable=False)
    schedule_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.session_schedules.id", ondelete="SET NULL"), nullable=True)
    gym_id = Column(Integer, nullable=False)
    client_id = Column(Integer, nullable=False)
    session_id = Column(Integer, nullable=False)
    trainer_id = Column(Integer, nullable=True)
    booking_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    status = Column(
        Enum("booked", "cancelled", "attended", "no_show", "refunded", name="session_booking_day_status"),
        default="booked",
        nullable=False,
    )
    checkin_token = Column(String(64), unique=True, nullable=True)
    scanned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("ix_session_booking_day_purchase", "purchase_id", "booking_date"),
        Index("ix_session_booking_day_schedule_date_status", "schedule_id", "booking_date", "status"),
        Index("ix_session_booking_day_client_status", "client_id", "status"),
        {"schema": SESSION_SCHEMA},
    )


class SessionBookingAudit(Base):
    """
    Audit trail for booking status changes and scans.
    """
    __tablename__ = "session_booking_audit"
    __table_args__ = {"schema": SESSION_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.session_purchases.id", ondelete="CASCADE"), nullable=False)
    booking_day_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.session_booking_days.id", ondelete="CASCADE"), nullable=True)
    event = Column(String(50), nullable=False)
    actor_role = Column(String(30), nullable=True)
    actor_id = Column(Integer, nullable=True)
    notes = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class SessionQrCode(Base):
    """
    QR codes issued for session check-ins.
    """
    __tablename__ = "session_qr_codes"
    __table_args__ = {"schema": SESSION_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    booking_day_id = Column(Integer, ForeignKey(f"{SESSION_SCHEMA}.session_booking_days.id", ondelete="CASCADE"), nullable=False)
    qr_code = Column(String(128), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)
    consumed_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
