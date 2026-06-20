"""Business logic for Session Bookings (checkout preview).

Orchestrates session pricing, intro-offer eligibility, fymble cash reward,
and billing breakdown — mirrors DailyPassBookingsService for sessions.
"""

from typing import List

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.reward_calculator import calculate_reward
from app.services.session_pricing_calculator import calculate_session_pricing

from ..sessions.repository import SessionRepository
from ..shared.session_pricing_service import SessionPricingService
from .repository import SessionBookingsRepository
from .schemas import (
    GymAddress,
    SessionBookingResponse,
    SessionSlotDetail,
)


class SessionBookingsService:
    """Calculate pricing + reward for a session booking."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.sess_repo = SessionRepository(db, redis)
        self.sess_pricing = SessionPricingService(db, redis)
        self.bookings_repo = SessionBookingsRepository(db)

    async def calculate_pricing(
        self,
        client_id: int,
        gym_id: int,
        session_id: int,
        schedule_id: int,
        dates: List[str],
    ) -> SessionBookingResponse:
        number_of_days = len(dates)

        # ── Sequential DB fetches (same AsyncSession can't run concurrent queries) ──
        gym_details = await self.bookings_repo.get_gym_details(gym_id)
        session_name = await self.bookings_repo.get_session_name(session_id)
        schedule_times = await self.bookings_repo.get_schedule_times(schedule_id)
        user_offer = await self.sess_repo.get_user_offer_eligibility(client_id)

        # ── Resolve session pricing for this gym ──
        actual_price, offer_price, offer_active = await self.sess_pricing.get_gym_pricing_breakdown(
            gym_id, session_id, client_id,
            user_sess_eligible=user_offer["session_offer_eligible"],
        )

        address = GymAddress(**gym_details["address"]) if gym_details.get("address") else None

        slot_detail = None
        if schedule_times:
            slot_detail = SessionSlotDetail(
                schedule_id=schedule_id,
                start_time=schedule_times["start_time"],
                end_time=schedule_times["end_time"],
            )

        if actual_price is None:
            return SessionBookingResponse(
                gym_name=gym_details["gym_name"],
                address=address,
                latitude=gym_details.get("latitude"),
                longitude=gym_details.get("longitude"),
                session_name=session_name,
                slot=slot_detail,
                number_of_days=number_of_days,
                dates=dates,
                session_offer_eligible=user_offer["session_offer_eligible"],
                session_count=user_offer["session_count"],
            )

        # ── Calculate pricing (offer vs actual) ──
        pricing = calculate_session_pricing(
            number_of_days=number_of_days,
            offer_price=offer_price,
            actual_price=actual_price,
            offer_active=offer_active,
            session_count=user_offer["session_count"],
        )

        # ── Reward calculation on total ──
        reward_info = await self.bookings_repo.get_reward_info(client_id)
        total_amount_minor = pricing["total_amount"] * 100
        reward_result = calculate_reward(
            amount_minor=total_amount_minor,
            available_cash_rupees=reward_info["available_cash_rupees"],
        )

        return SessionBookingResponse(
            gym_name=gym_details["gym_name"],
            address=address,
            latitude=gym_details.get("latitude"),
            longitude=gym_details.get("longitude"),
            session_name=session_name,
            slot=slot_detail,
            session_price=pricing["display_price"],
            actual_price=actual_price,
            number_of_days=number_of_days,
            dates=dates,
            total_amount=pricing["total_amount"],
            billing_lines=pricing["billing_lines"],
            show_modal=pricing["show_modal"],
            reward_amount=int(reward_result["reward_amount_rupees"]),
            opted_in=reward_info["opted_in"],
            session_offer_active=offer_active,
            session_offer_eligible=user_offer["session_offer_eligible"],
            session_count=user_offer["session_count"],
        )

