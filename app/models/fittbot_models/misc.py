from sqlalchemy import (
    Column, Integer, String, Float, Enum, Text, DateTime, ForeignKey, Date,
    Boolean, JSON, UniqueConstraint, Index, func,
)
from app.models.database import Base
from datetime import datetime


class Avatar(Base):
    __tablename__ = "avatar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gender = Column(String(45), nullable=False)
    avatarurl = Column(String(255), nullable=False)


class HomePoster(Base):
    __tablename__ = 'home_posters'

    id  = Column(Integer, primary_key=True, index=True)
    description=Column(String(45), nullable=False)
    url = Column(String(255), nullable=False)


class ManualPoster(Base):
    """Manual posters that override conditional frontend posters when show=True"""
    __tablename__ = 'manual_posters'

    id = Column(Integer, primary_key=True, index=True)
    urls = Column(JSON, nullable=False)  # JSON array: [{"url": "...", "description": "..."}, ...]
    show = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class OwnerHomePoster(Base):
    __tablename__ = "owner_home_posters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(45), nullable=True)
    url = Column(String(255), nullable=True)


class AppVersion(Base):
    __tablename__ = "app_versions"
    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(20))
    current_version = Column(String(20))
    min_supported_version = Column(String(20))
    force_update = Column(Boolean, default=False)
    update_url = Column(String(255), nullable=True)
    message = Column(String(255), nullable=True)
    button_label = Column(String(80), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AppRedirect(Base):
    """App redirect/maintenance modal configuration"""
    __tablename__ = "app_redirect"

    id = Column(Integer, primary_key=True, autoincrement=True)
    app = Column(String(45), nullable=False, index=True)
    type = Column(String(45), nullable=False)  # 'maintenance' or 'redirect'
    message = Column(Text, nullable=True)
    play_store_url = Column(String(255), nullable=True)
    app_store_url = Column(String(255), nullable=True)
    show = Column(Boolean, default=False, nullable=False)


class CharactersCombinationOld(Base):
    __tablename__ = "characters_combination_old"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    characters_id = Column(String(100), nullable=False)
    combination_id = Column(String(100), nullable=False)
    characters_url = Column(String(500), nullable=True)
    gender = Column(String(45), nullable=True)


class CharactersCombination(Base):
    __tablename__ = "characters_combination"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    characters_id = Column(String(100), nullable=False)
    combination_id = Column(String(100), nullable=False)
    characters_url = Column(String(500), nullable=True)
    gender = Column(String(45), nullable=True)


class FittbotCharacters(Base):
    __tablename__ = "fittbot_characters"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    character_id = Column(String(45), nullable=False, unique=True)
    character_url = Column(String(500), nullable=True)
    gender = Column(String(45), nullable=True)


class AppOpen(Base):
    __tablename__ = "app_open"

    id = Column(Integer, primary_key=True, autoincrement=True)
    open_time = Column(DateTime, default=datetime.now, nullable=False)
    device_id = Column(String(255), nullable=False, index=True)
    device_data = Column(JSON, nullable=True)
    platform = Column(String(50), nullable=True)


class ActiveUser(Base):
    __tablename__ = "active_users"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AIConsent(Base):
    """AI consent tracking for clients"""
    __tablename__ = "ai_consent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    consent = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AIReports(Base):
    """AI reports for clients"""
    __tablename__ = "ai_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=True)
    template = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class StepConsent(Base):
    """Step consent tracking for clients"""
    __tablename__ = "step_consent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    consent = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Royalty(Base):
    __tablename__ = "royalty"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id = Column(String(100), nullable=False)
    date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class RoyaltyStatus(Base):
    __tablename__ = "royalty_status"
    __table_args__ = (
        UniqueConstraint("gym_id", "month", name="uq_royalty_status_gym_month"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    month = Column(String(20), nullable=False, index=True)
    payment_status = Column(String(50), nullable=False, default="not_initiated")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class OwnerModalTracker(Base):
    __tablename__ = "owner_modal_tracker"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    last_modal_index = Column(Integer, default=0, nullable=False)  # Index in the missing features list
    last_shown_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GymJoinRequest(Base):
    __tablename__ = "gym_join_requests"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True)
    gym_id = Column(Integer, ForeignKey("gyms.gym_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    mobile_number = Column(String(15), nullable=False)
    alternate_mobile_number = Column(String(15), nullable=True)
    dp = Column(String(500), nullable=True)
    status = Column(String(50), nullable=False, default="pending", index=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
