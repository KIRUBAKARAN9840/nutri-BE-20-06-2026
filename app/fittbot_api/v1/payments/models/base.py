"""Base database models and configuration"""

from datetime import datetime, timezone, timedelta
from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.database import Base  # Use main app's Base class

from app.services.timezone_utils import IST, now_ist


class TimestampMixin:
    """Mixin for automatic timestamp management with IST timezone"""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_ist,  # Use IST instead of func.now()
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_ist,  # Use IST instead of func.now()
        onupdate=now_ist,  # Use IST for updates too
        nullable=False
    )