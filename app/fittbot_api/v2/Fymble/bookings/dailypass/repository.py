"""Database queries for Daily Pass bookings.

Fetches active passes, remaining day counts, reschedule eligibility,
and day-level details for a given client.
"""

from datetime import date, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dailypass_models import (
    DailyPass,
    DailyPassDay,
    DailyPassAudit,
    get_price_for_gym_async,
)


class DailyPassBookingRepository:
    """Daily-pass booking data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_passes(self, client_id: int, today: date) -> List[DailyPass]:
        """All passes whose valid_until >= today, newest first."""
        result = await self.db.execute(
            select(DailyPass)
            .where(DailyPass.client_id == client_id, today <= DailyPass.valid_until)
            .order_by(DailyPass.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_remaining_count(self, pass_id: str, today: date) -> int:
        """Count of future/today days still scheduled."""
        result = await self.db.execute(
            select(func.count(DailyPassDay.id)).where(
                DailyPassDay.pass_id == pass_id,
                DailyPassDay.scheduled_date >= today,
                DailyPassDay.status.in_(["scheduled", "available", "rescheduled"]),
            )
        )
        return result.scalar() or 0

    async def get_next_dates(self, pass_id: str, today: date, limit: int = 5) -> List[str]:
        """Next N upcoming scheduled dates as ISO strings."""
        result = await self.db.execute(
            select(DailyPassDay.scheduled_date)
            .where(
                DailyPassDay.pass_id == pass_id,
                DailyPassDay.scheduled_date >= today,
                DailyPassDay.status.in_(["scheduled", "available", "rescheduled"]),
            )
            .order_by(DailyPassDay.scheduled_date.asc())
            .limit(limit)
        )
        return [d.isoformat() for d in result.scalars().all()]

    async def has_audit_action(self, pass_id: str, action: str) -> bool:
        """Check if an audit action (reschedule/upgrade) exists."""
        result = await self.db.execute(
            select(DailyPassAudit.id)
            .where(DailyPassAudit.pass_id == pass_id, DailyPassAudit.action == action)
            .limit(1)
        )
        return result.scalars().first() is not None

    async def can_reschedule(self, pass_id: str, today: date) -> bool:
        """Determine if a pass is eligible for rescheduling."""
        if await self.has_audit_action(pass_id, "reschedule"):
            return False

        # Must have at least one future day (tomorrow+)
        future_result = await self.db.execute(
            select(func.count(DailyPassDay.id)).where(
                DailyPassDay.pass_id == pass_id,
                DailyPassDay.scheduled_date >= (today + timedelta(days=1)),
                DailyPassDay.status.in_(["scheduled", "available", "rescheduled"]),
            )
        )
        if (future_result.scalar() or 0) == 0:
            return False

        # Not all days attended
        total_result = await self.db.execute(
            select(func.count(DailyPassDay.id)).where(DailyPassDay.pass_id == pass_id)
        )
        attended_result = await self.db.execute(
            select(func.count(DailyPassDay.id)).where(
                DailyPassDay.pass_id == pass_id,
                DailyPassDay.status == "attended",
            )
        )
        total = total_result.scalar() or 0
        attended = attended_result.scalar() or 0
        if total > 0 and attended == total:
            return False

        return True

    async def get_day_breakdown(self, pass_id: str) -> Tuple[List[str], List[str]]:
        """Return (actual_days, rescheduled_days) for edited passes."""
        result = await self.db.execute(
            select(DailyPassDay)
            .where(DailyPassDay.pass_id == pass_id)
            .order_by(DailyPassDay.scheduled_date.asc())
        )
        actual, rescheduled = [], []
        for day in result.scalars().all():
            if day.reschedule_count and day.reschedule_count > 0:
                rescheduled.append(day.scheduled_date.isoformat())
            else:
                actual.append(day.scheduled_date.isoformat())
        return actual, rescheduled

    async def get_all_booked_dates(self, pass_id: str) -> List[str]:
        """All scheduled dates for a pass, sorted ascending."""
        result = await self.db.execute(
            select(DailyPassDay.scheduled_date)
            .where(
                DailyPassDay.pass_id == pass_id,
                DailyPassDay.status.in_(["scheduled", "available", "rescheduled", "attended"]),
            )
            .order_by(DailyPassDay.scheduled_date.asc())
        )
        return [d.isoformat() for d in result.scalars().all()]

    async def get_current_or_next_day_id(self, pass_id: str, today: date) -> Optional[str]:
        """Return day_id for today if scheduled, otherwise the nearest future day."""
        result = await self.db.execute(
            select(DailyPassDay.id)
            .where(
                DailyPassDay.pass_id == pass_id,
                DailyPassDay.scheduled_date >= today,
                DailyPassDay.status.in_(["scheduled", "available", "rescheduled"]),
            )
            .order_by(DailyPassDay.scheduled_date.asc())
            .limit(1)
        )
        return result.scalar()

    async def get_display_price(self, gym_id: int, fallback_amount: Optional[float]) -> float:
        """Get per-day display price (with markup), falling back to paid amount."""
        try:
            price_minor = await get_price_for_gym_async(self.db, int(gym_id))
            return price_minor / 100
        except Exception:
            return (fallback_amount or 0) / 100
