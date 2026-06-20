from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, ForeignKey, Date,
    Boolean, JSON, Index, UniqueConstraint,
)
from app.models.database import Base
from datetime import datetime
import uuid


class RewardQuest(Base):
    __tablename__ = 'reward_quest'
    id = Column(Integer, primary_key=True, autoincrement=True)
    xp = Column(Integer, nullable=False)
    about = Column(String(255))
    description = Column(Text)
    tag = Column(String(45))


class RewardGym(Base):
    __tablename__ = 'reward_gym'
    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, nullable=False)
    xp = Column(Integer, nullable=False)
    gift = Column(String(500))
    image= Column(String(255))


class RewardClientHistory(Base):
    __tablename__ = 'reward_client_history'
    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=False)
    date = Column(Date)
    xp = Column(Integer, nullable=False)
    gift = Column(String(255))


class LeaderboardDaily(Base):
    __tablename__ = 'rewards_leaderboard_daily'
    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=True)
    xp = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)


class LeaderboardMonthly(Base):
    __tablename__ = 'rewards_leaderboard_monthly'
    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=True)
    xp = Column(Integer, nullable=False)
    month = Column(Date, nullable=False)


class LeaderboardOverall(Base):
    __tablename__ = 'rewards_leaderboard_overall'
    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False)
    gym_id = Column(Integer, nullable=True)
    xp = Column(Integer, nullable=False)


class RewardBadge(Base):
    __tablename__ = 'rewards_badges'

    id = Column(Integer, primary_key=True, autoincrement=True)
    badge = Column(String(50), nullable=False)
    min_points = Column(Integer, nullable=False)
    max_points = Column(Integer, nullable=False)
    image_url = Column(String(255), nullable=False)
    level = Column(String(10), nullable=False)


class RewardPrizeHistory(Base):
    __tablename__ = 'reward_prize_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, nullable=False)
    client_id = Column(Integer, nullable=False)
    xp = Column(Integer, nullable=False)
    gift = Column(String(255), nullable=False )
    achieved_date = Column(DateTime,nullable=False)
    given_date = Column(DateTime,nullable=True)
    client_name = Column(String(50), nullable=False)
    is_given = Column(Boolean,nullable=False)
    profile= Column(String(155))


class RewardInterest(Base):
    __tablename__='reward_interest'
    id  = Column(Integer, primary_key=True, index=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey('clients.client_id', ondelete='CASCADE', onupdate='CASCADE'),unique=True, nullable=True)
    interested = Column(Boolean)
    next_reminder = Column(DateTime, nullable=True)


class RewardProgramOptIn(Base):
    """
    Tracks which clients have opted into the Fymble Mega Fitness Rewards Program.
    A client must explicitly opt-in to participate.
    """
    __tablename__ = "reward_program_opt_ins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    opted_in_at = Column(DateTime, default=datetime.now, nullable=False)
    status = Column(String(20), default="active", nullable=False)  # active, withdrawn
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("ix_reward_opt_in_status", "status"),
    )


class RewardProgramEntry(Base):
    """
    Stores individual reward entries earned by clients.
    Each eligible purchase generates unique Entry IDs based on the purchase type.

    Entry limits per user:
    - Daily Gym Pass: Up to 100 entries
    - Session Booking: Up to 100 entries
    - Fymble Subscription: Up to 8 entries (2 per month)
    - Referral Bonus: Up to 25 entries (1 per 3 referrals)
    """
    __tablename__ = "reward_program_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(String(36), unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    method = Column(String(50), nullable=False, index=True)  # dailypass, session, subscription, referral
    source_id = Column(String(100), nullable=True)  # purchase_id, payment_id, etc. for traceability
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    status = Column(String(20), default="valid", nullable=False)  # valid, cancelled, winner

    __table_args__ = (
        Index("ix_reward_entry_client_method", "client_id", "method"),
        Index("ix_reward_entry_created", "created_at"),
    )
