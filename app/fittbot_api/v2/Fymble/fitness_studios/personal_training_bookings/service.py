"""Business logic for Personal Training Bookings (checkout preview).

Orchestrates PT pricing, intro-offer eligibility, trainer details,
fymble cash reward, and billing breakdown.
Same pricing calculator as sessions (₹99 intro offer, 3-session cap).
"""

from typing import List

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.reward_calculator import calculate_reward
from app.services.session_pricing_calculator import calculate_session_pricing

from ..personal_training.repository import PTRepository, SESSION_OFFER_PRICE
from ..shared.session_pricing_service import SessionPricingService
from .repository import PTBookingsRepository
from .schemas import (
    GymAddress,
    PTBookingResponse,
    PTSlotDetail,
    PTTrainerDetail,
)


class PTBookingsService:
    """Calculate pricing + reward for a personal training booking."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.pt_repo = PTRepository(db, redis)
        self.bookings_repo = PTBookingsRepository(db)

    async def calculate_pricing(
        self,
        client_id: int,
        gym_id: int,
        trainer_id: int,
        schedule_id: int,
        dates: List[str],
    ) -> PTBookingResponse:
        number_of_days = len(dates)

        # ── Sequential DB fetches (same AsyncSession) ──
        gym_details = await self.bookings_repo.get_gym_details(gym_id)
        trainer_profile = await self.bookings_repo.get_trainer_profile(gym_id, trainer_id)
        schedule_times = await self.bookings_repo.get_schedule_times(schedule_id)
        actual_price = await self.bookings_repo.get_pt_setting(gym_id, trainer_id)
        user_offer = await self.pt_repo.get_user_offer_eligibility(client_id)

        # ── Offer eligibility for this gym ──
        offer_map = await self.pt_repo.fetch_offer_flags([gym_id])
        promo_counts = await self.pt_repo.fetch_promo_counts([gym_id])
        booked_gyms = await self.pt_repo.fetch_user_booked_promo_gyms(client_id, [gym_id])

        offer_active = SessionPricingService.is_offer_active(
            gym_id, offer_map, promo_counts, booked_gyms,
            user_sess_eligible=user_offer["session_offer_eligible"],
        )

        address = GymAddress(**gym_details["address"]) if gym_details.get("address") else None

        trainer_detail = None
        if trainer_profile:
            trainer_detail = PTTrainerDetail(**trainer_profile)

        slot_detail = None
        if schedule_times:
            slot_detail = PTSlotDetail(
                schedule_id=schedule_id,
                start_time=schedule_times["start_time"],
                end_time=schedule_times["end_time"],
            )

        if actual_price is None:
            return PTBookingResponse(
                gym_name=gym_details["gym_name"],
                address=address,
                latitude=gym_details.get("latitude"),
                longitude=gym_details.get("longitude"),
                trainer=trainer_detail,
                slot=slot_detail,
                number_of_days=number_of_days,
                dates=dates,
                session_offer_eligible=user_offer["session_offer_eligible"],
                session_count=user_offer["session_count"],
            )

        offer_price = SESSION_OFFER_PRICE if offer_active else actual_price

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

        return PTBookingResponse(
            gym_name=gym_details["gym_name"],
            address=address,
            latitude=gym_details.get("latitude"),
            longitude=gym_details.get("longitude"),
            trainer=trainer_detail,
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
