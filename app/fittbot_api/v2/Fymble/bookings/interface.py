"""
Bookings Module — Public Interface
====================================
Other modules must use this class to interact with booking functionality.
Do NOT import repository, shared helpers, or internal services directly.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v2.Fymble.bookings.dailypass.service import DailyPassBookingService
from app.fittbot_api.v2.Fymble.bookings.sessions.service import SessionBookingService
from app.fittbot_api.v2.Fymble.bookings.gym_membership.service import GymMembershipBookingService


class BookingsModule:
    """Entry point for other modules to access booking functionality."""

    def __init__(self, db: AsyncSession):
        self._dailypass = DailyPassBookingService(db)
        self._sessions = SessionBookingService(db)
        self._gym_membership = GymMembershipBookingService(db)

    # ── Daily Pass ──

    async def list_active_dailypasses(self, client_id: int):
        return await self._dailypass.list_active(client_id)

    # ── Sessions ──

    async def get_upcoming_sessions(self, client_id: int):
        return await self._sessions.get_upcoming(client_id)

    # ── Gym Membership ──

    async def list_active_memberships(self, client_id: int):
        return await self._gym_membership.list_active(client_id)
