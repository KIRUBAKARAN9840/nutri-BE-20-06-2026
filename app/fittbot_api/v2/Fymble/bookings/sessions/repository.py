"""Database queries for Session upcoming bookings.

Fetches booked session days joined with purchases, and session metadata.
Reuses shared GymInfoRepository for gym details.
"""

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import (
    SessionBookingDay,
    SessionPurchase,
    ClassSession,
)
from app.models.fittbot_models.trainer import Trainer


class SessionBookingRepository:
    """Session-booking data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_upcoming_bookings(
        self, client_id: int, today: date
    ) -> Sequence[Any]:
        """Upcoming booked session days (paid, booked, future), newest purchase first."""
        stmt = (
            select(SessionBookingDay, SessionPurchase)
            .join(SessionPurchase, SessionPurchase.id == SessionBookingDay.purchase_id)
            .where(
                SessionBookingDay.client_id == client_id,
                SessionBookingDay.booking_date >= today,
                SessionBookingDay.status == "booked",
                SessionPurchase.status == "paid",
            )
            .order_by(
                SessionPurchase.created_at.desc(),
                SessionBookingDay.booking_date.asc(),
            )
        )
        result = await self.db.execute(stmt)
        return result.all()

    async def get_trainers_map(self, trainer_ids: List[int]) -> Dict[int, str]:
        """Map of trainer_id -> full_name for bulk lookup."""
        if not trainer_ids:
            return {}
        result = await self.db.execute(
            select(Trainer.trainer_id, Trainer.full_name)
            .where(Trainer.trainer_id.in_(trainer_ids))
        )
        return {r.trainer_id: r.full_name for r in result.all()}

    async def get_sessions_map(self, session_ids: List[int]) -> Dict[int, Any]:
        """Map of session_id -> ClassSession for name lookup."""
        if not session_ids:
            return {}
        result = await self.db.execute(
            select(ClassSession).where(ClassSession.id.in_(session_ids))
        )
        return {s.id: s for s in result.scalars().all()}

    @staticmethod
    def resolve_session_name(session_meta: Optional[Any]) -> str:
        """Extract display name from a ClassSession row."""
        if not session_meta:
            return "Session"
        name = session_meta.internal if session_meta.internal else session_meta.name
        return "personal_training" if name == "personal_training_session" else (name or "Session")

    @staticmethod
    def format_time(t: Any) -> Optional[str]:
        """Format a time object to '05:00 PM'."""
        if t is None:
            return None
        if hasattr(t, "strftime"):
            return t.strftime("%I:%M %p")
        return str(t)
