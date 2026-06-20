from sqlalchemy import (
    Column, Integer, BigInteger, String, DateTime, ForeignKey, Date, Time,
    Boolean, JSON, Index,
)
from app.models.database import Base
from datetime import datetime


class Attendance(Base):
    __tablename__ = "attendance"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    in_time = Column(Time, nullable=False)
    out_time = Column(Time)
    muscle=Column(JSON)
    in_time_2 = Column(Time)
    out_time_2= Column(Time)
    muscle_2  = Column(JSON)
    in_time_3 = Column(Time)
    out_time_3= Column(Time)
    muscle_3 = Column(JSON)

    __table_args__ = (
        Index("ix_attendance_gym_date", "gym_id", "date"),
        Index("ix_attendance_client_date", "client_id", "date"),
    )


class AttendanceGym(Base):
    __tablename__ = 'attendance_gym'

    id               = Column(BigInteger, primary_key=True, autoincrement=True)
    gym_id           = Column(
                          Integer,
                          ForeignKey('gyms.gym_id', ondelete='CASCADE', onupdate='CASCADE'),
                          nullable=False
                      )
    date             = Column(Date,    nullable=False)
    attendance_count = Column(Integer, nullable=False)


class AttendanceStreak(Base):
    __tablename__ = "attendance_streak"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False)
    current_streak_days = Column(Integer, default=0)
    last_attendance_date = Column(Date, nullable=True)
    last_xp_awarded_at = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GymHourlyAgg(Base):
    __tablename__ = "gym_hourly_agg"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    col_4_6 = Column("4-6", Integer, default=0, nullable=False)
    col_6_8 = Column("6-8", Integer, default=0, nullable=False)
    col_8_10 = Column("8-10", Integer, default=0, nullable=False)
    col_10_12 = Column("10-12", Integer, default=0, nullable=False)
    col_12_14 = Column("12-14", Integer, default=0, nullable=False)
    col_14_16 = Column("14-16", Integer, default=0, nullable=False)
    col_16_18 = Column("16-18", Integer, default=0, nullable=False)
    col_18_20 = Column("18-20", Integer, default=0, nullable=False)
    col_20_22 = Column("20-22", Integer, default=0, nullable=False)
    col_22_24 = Column("22-24", Integer, default=0, nullable=False)


class DailyGymHourlyAgg(Base):
    __tablename__ = "daily_gym_hourly_agg"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    agg_date = Column(Date, nullable=False)

    col_4_6 = Column("4-6", Integer, default=0, nullable=False)
    col_6_8 = Column("6-8", Integer, default=0, nullable=False)
    col_8_10 = Column("8-10", Integer, default=0, nullable=False)
    col_10_12 = Column("10-12", Integer, default=0, nullable=False)
    col_12_14 = Column("12-14", Integer, default=0, nullable=False)
    col_14_16 = Column("14-16", Integer, default=0, nullable=False)
    col_16_18 = Column("16-18", Integer, default=0, nullable=False)
    col_18_20 = Column("18-20", Integer, default=0, nullable=False)
    col_20_22 = Column("20-22", Integer, default=0, nullable=False)
    col_22_24 = Column("22-24", Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
