"""Business logic for Gym Membership bookings.

Orchestrates repository queries to build the list
of active gym membership cards for a client.
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.pricing import get_markup_multiplier
from app.fittbot_api.v1.client.client_api.home.gym_studios import smart_round_price

from .repository import GymMembershipBookingRepository
from .schemas import (
    GymMembershipData,
    GymMembershipListResponse,
    MembershipCard,
    NutritionalPlan,
)

logger = logging.getLogger("bookings.gym_membership.service")

def _calculate_nutritional_plan(duration: int) -> Optional[NutritionalPlan]:
    """Same rule as fitness_studios/gym_membership service."""
    if duration >= 12:
        return NutritionalPlan(consultations=3, amount=3600)
    elif duration >= 6:
        return NutritionalPlan(consultations=2, amount=2400)
    elif duration <= 5:
        return NutritionalPlan(consultations=1, amount=1200)
    return None


class GymMembershipBookingService:
    """List active gym membership bookings for a client."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = GymMembershipBookingRepository(db)

    async def list_active(self, client_id: int) -> GymMembershipListResponse:
        # ── Client profile ──
        client = await self.repo.get_client(client_id)

        # Top-level gym_id / gym_name (client's current gym)
        gym_id = None
        gym_name = None
        if client and client.gym_id:
            gym = await self.repo.get_gym(client.gym_id)
            if gym:
                gym_id = gym.gym_id
                gym_name = gym.name

        # ── Memberships ──
        memberships = await self.repo.get_active_memberships(client_id)

        # ── Batch-fetch plans ──
        plan_ids = {m.plan_id for m in memberships if m.plan_id}
        plans_map = await self.repo.get_bulk_plans(plan_ids)

        # ── Batch-fetch gym names ──
        card_gym_ids = {int(m.gym_id) for m in memberships if m.gym_id}
        gyms_map = await self.repo.get_bulk_gym_names(card_gym_ids)

        # ── Build cards ──
        membership_cards = []
        for m in memberships:
            plan = plans_map.get(m.plan_id) if m.plan_id else None
            duration = plan.duration if plan else None

            # Price: try OrderItem, fallback to plan * markup
            amount = None
            if m.entitlement_id:
                amount = await self.repo.get_paid_amount(m.entitlement_id)
            if amount is None and plan and plan.amount:
                amount = smart_round_price(plan.amount * get_markup_multiplier())

            # Pause logic (same as V1)
            avail_pause = False
            if plan and plan.pause is not None and str(plan.pause).strip() not in ("", "false"):
                avail_pause = True

            pause_available = (
                avail_pause
                and (m.pause in ("0", "False", "", None))
            )
            continue_available = (
                m.pause is not None and str(m.pause).lower() == "taken"
            )

            # Nutritional plan
            nutritional_plan = _calculate_nutritional_plan(duration) if duration else None

            card_gym_name = gyms_map.get(int(m.gym_id)) if m.gym_id else None

            membership_cards.append(
                MembershipCard(
                    membership_id=m.id,
                    gym_id=m.gym_id,
                    gym_name=card_gym_name,
                    amount=amount,
                    duration=duration if m.plan_id else None,
                    purchased_at=m.purchased_at.isoformat() if m.purchased_at else None,
                    type=m.type,
                    status=m.status,
                    entitlement_id=m.entitlement_id,
                    expires_at=m.expires_at.isoformat() if m.expires_at else None,
                    bonus=plan.bonus if plan else None,
                    bonus_type=plan.bonus_type if plan else None,
                    pause_available=pause_available,
                    pause=plan.pause if plan else None,
                    pause_type=plan.pause_type if plan else None,
                    continue_available=continue_available,
                    nutritional_plan=nutritional_plan,
                )
            )

        # ── Top-level membership type/status from newest ──
        membership_type = memberships[0].type if memberships else "normal"
        membership_status = memberships[0].status if memberships else None

        response = GymMembershipListResponse(
            data=GymMembershipData(
                profile=client.profile if client else None,
                name=client.name if client else None,
                client_id=client_id,
                contact=client.contact if client else None,
                gender=client.gender if client else None,
                uuid=str(client.uuid_client) if client else None,
                gym_id=gym_id,
                gym_name=gym_name,
                type=membership_type,
                membership_status=membership_status,
                membership_cards=membership_cards,
            )
        )

        return response
