"""Database queries for Gym Membership Bookings (checkout preview).

Fetches gym details, plan info, reward opt-in, and fittbot cash.
"""

from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.fittbot_models import GymPlans, NoCostEmi, RewardProgramOptIn
from ..shared.utils import fetch_active_membership_offers, fetch_gym_address_and_location
from app.models.fittbot_models.referral import ReferralFittbotCash


class MembershipBookingsRepository:
    """Gym-membership-bookings data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_gym_details(self, gym_id: int) -> Dict:
        """Fetch gym name, logo, address, and coordinates."""
        return await fetch_gym_address_and_location(self.db, gym_id)

    async def get_plan(self, plan_id: int) -> Optional[GymPlans]:
        """Fetch a single GymPlans row by ID."""
        result = await self.db.execute(
            select(GymPlans).where(GymPlans.id == plan_id)
        )
        return result.scalars().first()

    async def get_no_cost_emi(self, gym_id: int) -> bool:
        """Check if gym has no-cost EMI enabled."""
        result = await self.db.execute(
            select(NoCostEmi).where(
                NoCostEmi.gym_id == gym_id,
                NoCostEmi.no_cost_emi.is_(True),
            ).limit(1)
        )
        return result.scalars().first() is not None

    async def get_active_offer(self, gym_id: int, plan_id: int):
        """Get active offer for a specific plan. Plan-specific wins over gym-wide."""
        offers_by_gym = await fetch_active_membership_offers(self.db, [gym_id])
        gym_offers = offers_by_gym.get(gym_id, {})
        # Plan-specific offer takes priority over gym-wide (None key)
        return gym_offers.get(plan_id) or gym_offers.get(None)

    async def get_reward_info(self, client_id: int) -> Dict:
        """Fetch reward opt-in status and available fittbot cash for a client."""
        opt_in_result = await self.db.execute(
            select(RewardProgramOptIn).where(
                RewardProgramOptIn.client_id == client_id
            )
        )
        cash_result = await self.db.execute(
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
