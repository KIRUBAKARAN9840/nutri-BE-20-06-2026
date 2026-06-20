"""
Fitness Studios Module — Public Interface
==========================================
Other modules must use these classes to interact with fitness studio functionality.
Do NOT import repository, shared helpers, or internal services directly.
"""

from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.fittbot_api.v2.Fymble.fitness_studios.gym_membership.service import MembershipService
from app.fittbot_api.v2.Fymble.fitness_studios.sessions.service import SessionService
from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.service import DailyPassService
from app.fittbot_api.v2.Fymble.fitness_studios.gym_membership_bookings.service import MembershipBookingsService
from app.fittbot_api.v2.Fymble.fitness_studios.session_bookings.service import SessionBookingsService
from app.fittbot_api.v2.Fymble.fitness_studios.dailypass_bookings.service import DailyPassBookingsService
from app.fittbot_api.v2.Fymble.fitness_studios.personal_training.service import PTService


class FitnessStudiosModule:
    """Entry point for other modules to access fitness studio functionality."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self._membership = MembershipService(db, redis)
        self._sessions = SessionService(db, redis)
        self._daily_pass = DailyPassService(db, redis)
        self._membership_bookings = MembershipBookingsService(db, redis)
        self._session_bookings = SessionBookingsService(db, redis)
        self._dailypass_bookings = DailyPassBookingsService(db, redis)
        self._personal_training = PTService(db, redis)

    # ── Listings ──

    async def list_membership_gyms(self, params):
        return await self._membership.list_gyms(params)

    async def list_session_gyms(self, params):
        return await self._sessions.list_gyms(params)

    async def list_dailypass_gyms(self, params):
        return await self._daily_pass.list_gyms(params)

    async def list_personal_training_gyms(self, params):
        return await self._personal_training.list_gyms(params)

    # ── Gym Details ──

    async def get_membership_gym_details(self, gym_id: int):
        return await self._membership.get_gym_details(gym_id)

    async def get_dailypass_gym_details(self, gym_id: int):
        return await self._daily_pass.get_gym_details(gym_id)

    # ── Booking Calculations ──

    async def calculate_membership_pricing(self, client_id: int, gym_id: int, plan_id: int):
        return await self._membership_bookings.calculate_pricing(client_id, gym_id, plan_id)

    async def calculate_session_pricing(
        self, client_id: int, gym_id: int, session_id: int, schedule_id: int, dates: List[str]
    ):
        return await self._session_bookings.calculate_pricing(
            client_id, gym_id, session_id, schedule_id, dates
        )

    async def calculate_dailypass_reward(
        self, client_id: int, gym_id: int, number_of_days: int, head_count: Optional[int] = None
    ):
        return await self._dailypass_bookings.calculate_reward(
            client_id, gym_id, number_of_days, head_count
        )
