"""Database queries specific to Session Bookings (checkout preview).

Fetches gym details, session schedule info, and reward-related data.
Reuses SessionRepository for offer eligibility and pricing queries.
"""

from datetime import time
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import RewardProgramOptIn
from app.models.fittbot_models.referral import ReferralFittbotCash
from app.models.fittbot_models import ClassSession, SessionSchedule

from ..shared.utils import fetch_gym_address_and_location, to_12hr


class SessionBookingsRepository:
    """Session-bookings data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_gym_details(self, gym_id: int) -> Dict:
        """Fetch gym name, address, and coordinates."""
        return await fetch_gym_address_and_location(self.db, gym_id)

    async def get_session_name(self, session_id: int) -> Optional[str]:
        """Fetch session name by ID."""
        result = await self.db.execute(
            select(ClassSession.name).where(ClassSession.id == session_id)
        )
        return result.scalar()

    async def get_schedule_times(self, schedule_id: int) -> Optional[Dict]:
        """Fetch start_time and end_time for a schedule."""
        result = await self.db.execute(
            select(
                SessionSchedule.start_time,
                SessionSchedule.end_time,
            ).where(SessionSchedule.id == schedule_id)
        )
        row = result.first()
        if not row:
            return None

        start = row.start_time
        end = row.end_time

        return {
            "start_time": to_12hr(
                start.strftime("%H:%M") if isinstance(start, time) else str(start)[:5]
            ),
            "end_time": to_12hr(
                end.strftime("%H:%M") if isinstance(end, time) else str(end)[:5]
            ),
        }

    async def get_reward_info(self, client_id: int) -> Dict:
        """Fetch reward opt-in status and available fittbot cash for a client."""
        opt_in_result, cash_result = await self.db.execute(
            select(RewardProgramOptIn).where(
                RewardProgramOptIn.client_id == client_id
            )
        ), await self.db.execute(
            select(ReferralFittbotCash).where(
                ReferralFittbotCash.client_id == client_id
            )
        )

        opt_in = opt_in_result.scalars().first()
        cash_entry = cash_result.scalars().first()

        return {
            "opted_in": bool(opt_in and opt_in.status == "active"),
            "available_cash_rupees": cash_entry.fittbot_cash if cash_entry else 0,
        }
