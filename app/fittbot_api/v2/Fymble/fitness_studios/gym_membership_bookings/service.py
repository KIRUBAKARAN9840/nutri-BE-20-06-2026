

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.pricing import (
    get_daily_offer_discount, get_markup_multiplier,
    get_walkaway_redis_key, apply_walkaway_discount, WALKAWAY_DISCOUNT_PERCENT,
)
from app.services.reward_calculator import REWARD_CAP_MINOR, calculate_reward

from ..shared.schemas import GymAddress
from ..shared.utils import apply_coupon_discount, smart_round_price, validate_coupon
from .repository import MembershipBookingsRepository
from .schemas import MembershipBookingResponse


class MembershipBookingsService:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.repo = MembershipBookingsRepository(db)

    async def calculate_pricing(
        self,
        client_id: int,
        gym_id: int,
        plan_id: int,
        coupon_code: str = None,
    ) -> MembershipBookingResponse:

        gym_details = await self.repo.get_gym_details(gym_id)
        plan = await self.repo.get_plan(plan_id)

        address = GymAddress(**gym_details["address"]) if gym_details.get("address") else None

        if not plan:
            return MembershipBookingResponse(
                gym_name=gym_details["gym_name"],
                gym_logo=gym_details.get("gym_logo"),
                address=address,
                latitude=gym_details.get("latitude"),
                longitude=gym_details.get("longitude"),
                plan_id=plan_id,
            )

        # ── Resolve offer price ──
        offer = await self.repo.get_active_offer(gym_id, plan_id)
        offer_active = offer is not None
        base_amount = offer.offer_price if offer_active else plan.amount

        # ── Apply markup ──
        multiplier = get_markup_multiplier()
        increased_amount = smart_round_price(base_amount * multiplier)
        original_amount_before_offer = (
            smart_round_price(plan.amount * multiplier) if offer_active else None
        )

        # ── Daily offer discount (even-date) ──
        daily_discount = get_daily_offer_discount()
        daily_offer_active = daily_discount > 0
        if daily_offer_active:
            increased_amount = max(increased_amount - daily_discount, 0)

        # ── Walkaway 5% discount ──
        walkaway_key = get_walkaway_redis_key(client_id)
        walkaway_active = bool(await self.redis.exists(walkaway_key))
        walkaway_discount_amount = 0
        if walkaway_active:
            amount_before_walkaway = increased_amount
            increased_amount = apply_walkaway_discount(increased_amount)
            walkaway_discount_amount = amount_before_walkaway - increased_amount

        # ── Telecaller coupon discount ──
        coupon_applied = False
        coupon_discount_percent = 0
        coupon_discount_amount = None
        amount_before_coupon = None
        coupon_message = None

        if coupon_code:
            coupon_info = await validate_coupon(self.db, coupon_code, client_id)
            if coupon_info:
                coupon_applied = True
                coupon_discount_percent = coupon_info["discount_percent"]
                amount_before_coupon = increased_amount
                increased_amount = apply_coupon_discount(increased_amount, coupon_discount_percent)
                coupon_discount_amount = amount_before_coupon - increased_amount
                coupon_message = f"Coupon applied! You save ₹{coupon_discount_amount}"
            else:
                coupon_message = "Invalid or expired coupon code"

        # ── No-cost EMI check ──
        gym_has_emi = await self.repo.get_no_cost_emi(gym_id)
        plan_no_cost_emi = gym_has_emi and increased_amount >= 4000

        # ── Reward calculation (same as session_bookings) ──
        reward_info = await self.repo.get_reward_info(client_id)
        total_amount_minor = increased_amount * 100
        
        reward_result = calculate_reward(
            amount_minor=total_amount_minor,
            available_cash_rupees=reward_info["available_cash_rupees"],
            max_cap_minor=REWARD_CAP_MINOR,
        )

        return MembershipBookingResponse(
            gym_name=gym_details["gym_name"],
            gym_logo=gym_details.get("gym_logo"),
            address=address,
            latitude=gym_details.get("latitude"),
            longitude=gym_details.get("longitude"),
            plan_name=plan.plans,
            plan_id=plan_id,
            duration=plan.duration,
            amount=increased_amount,
            original_amount_before_offer=original_amount_before_offer,
            offer_active=offer_active,
            personal_training=plan.personal_training or False,
            plan_for=plan.plan_for,
            sessions_count=plan.sessions_count,
            no_cost_emi=plan_no_cost_emi,
            reward_amount=int(reward_result["reward_amount_rupees"]),
            opted_in=reward_info["opted_in"],
            daily_offer_active=daily_offer_active,
            walkaway_discount_active=walkaway_active,
            walkaway_discount_amount=walkaway_discount_amount,
            coupon_applied=coupon_applied,
            coupon_discount_percent=coupon_discount_percent,
            coupon_discount_amount=coupon_discount_amount,
            amount_before_coupon=amount_before_coupon,
            coupon_message=coupon_message,
        )
