"""Database queries for Gym Membership bookings.

Fetches active memberships, plan details, paid amounts,
and nutrition eligibility for a given client.
"""

from typing import Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.client import Client
from app.models.fittbot_models.gym import (
    FittbotGymMembership,
    Gym,
    GymPlans,
)
from app.fittbot_api.v1.payments.models.entitlements import Entitlement
from app.fittbot_api.v1.payments.models.orders import OrderItem
from app.models.nutrition_models import NutritionEligibility


class GymMembershipBookingRepository:
    """Gym membership booking data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_client(self, client_id: int) -> Optional[Client]:
        result = await self.db.execute(
            select(Client).where(Client.client_id == client_id)
        )
        return result.scalars().first()

    async def get_gym(self, gym_id: int) -> Optional[Gym]:
        result = await self.db.execute(
            select(Gym).where(Gym.gym_id == gym_id)
        )
        return result.scalars().first()

    async def get_active_memberships(self, client_id: int) -> List[FittbotGymMembership]:
        """Active/upcoming/paused memberships, newest first. Excludes admission_fees."""
        result = await self.db.execute(
            select(FittbotGymMembership)
            .where(
                FittbotGymMembership.client_id == str(client_id),
                FittbotGymMembership.status.in_(["upcoming", "active", "paused"]),
                FittbotGymMembership.type != "admission_fees",
            )
            .order_by(FittbotGymMembership.purchased_at.desc())
        )
        return list(result.scalars().all())

    async def get_bulk_plans(self, plan_ids: Set[int]) -> Dict[int, GymPlans]:
        """Batch-fetch plans by IDs."""
        if not plan_ids:
            return {}
        result = await self.db.execute(
            select(GymPlans).where(GymPlans.id.in_(list(plan_ids)))
        )
        return {p.id: p for p in result.scalars().all()}

    async def get_bulk_gym_names(self, gym_ids: Set[int]) -> Dict[int, str]:
        """Batch-fetch gym names by IDs."""
        if not gym_ids:
            return {}
        result = await self.db.execute(
            select(Gym.gym_id, Gym.name).where(Gym.gym_id.in_(list(gym_ids)))
        )
        return {r.gym_id: r.name for r in result.all()}

    async def get_paid_amount(self, entitlement_id: str) -> Optional[float]:
        """Trace entitlement -> order_item -> unit_price_minor, return rupees."""
        ent_result = await self.db.execute(
            select(Entitlement.order_item_id)
            .where(Entitlement.id == entitlement_id)
        )
        order_item_id = ent_result.scalar()
        if not order_item_id:
            return None

        oi_result = await self.db.execute(
            select(OrderItem.unit_price_minor)
            .where(OrderItem.id == order_item_id)
        )
        unit_price = oi_result.scalar()
        if unit_price is None:
            return None
        return unit_price // 100

    async def get_nutrition_eligibility(
        self, client_id: int, source_type: str = "gym_membership",
    ) -> Dict[str, NutritionEligibility]:
        """All nutrition eligibility records for this client+source_type, keyed by source_id."""
        result = await self.db.execute(
            select(NutritionEligibility).where(
                NutritionEligibility.client_id == client_id,
                NutritionEligibility.source_type == source_type,
            )
        )
        return {e.source_id: e for e in result.scalars().all()}
