from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Date, UniqueConstraint,
)
from app.models.database import Base
from datetime import datetime


class ReferralCode(Base):
    __tablename__ = "referral_code"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False, unique=True, index=True)
    referral_code = Column(String(50), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.now)


class ReferralMapping(Base):
    __tablename__ = "referral_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    referrer_id = Column(Integer, nullable=False, index=True)
    referee_id = Column(Integer, nullable=False, index=True)
    referral_date = Column(Date, nullable=False, default=lambda: datetime.now().date())
    status= Column(String(45), nullable=True)


class ReferralRedeem(Base):
    __tablename__ = "referral_redeem"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    points_redeemed = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class ReferralFittbotCash(Base):
    __tablename__ = "referral_fittbot_cash"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    fittbot_cash = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReferralFittbotCashLogs(Base):
    __tablename__ = "referral_fittbot_cash_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    fittbot_cash = Column(Integer, nullable=False)
    reason = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReferralGymCode(Base):
    __tablename__ = "referral_gym_code"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    referral_code = Column(String(50), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.now)


class ReferralGymCash(Base):
    __tablename__ = "referral_gym_cash"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, index=True)
    month = Column(Date, nullable=False)
    referral_cash = Column(Integer, nullable=False, default=0)
    status = Column(String(45), nullable=False, default="active")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReferralGymCashLogs(Base):
    __tablename__ = "referral_gym_cash_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, index=True)
    referral_cash = Column(Integer, nullable=False)
    reason = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReferralGymMapping(Base):
    __tablename__ = "referral_gym_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    referrer_owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, index=True)
    referee_owner_id = Column(Integer, ForeignKey("gym_owners.owner_id", ondelete="CASCADE"), nullable=False, index=True)
    referral_date = Column(Date, nullable=False, default=lambda: datetime.now().date())
    status = Column(String(45), nullable=True)
