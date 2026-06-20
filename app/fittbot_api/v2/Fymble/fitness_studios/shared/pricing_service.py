"""Reusable dailypass pricing resolution.

Extracts the price-calculation logic so it can be shared across
daily_pass (listing) and dailypass_bookings (checkout preview).
"""

import asyncio
from typing import Dict, List, Optional, Tuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.constants import GYM_OFFER_USER_CAP, OFFER_PRICE_PAISE, OFFER_PRICE_RUPEES
from app.config.pricing import get_markup_multiplier, compute_dailypass_price_rupees

from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.repository import (
    DAILYPASS_HASH_KEY,
    DailyPassRepository,
)


class PricingService:
    """Resolve the display price for a dailypass gym."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.dp_repo = DailyPassRepository(db, redis)

    # ── Single-gym price (used by dailypass_bookings) ─────────

    async def get_gym_dailypass_price(
        self,
        gym_id: int,
        user_dp_eligible: bool = False,
    ) -> Optional[int]:
        """Return the resolved dailypass price (rupees) for one gym."""

        pricing_map = await self.dp_repo.fetch_dailypass_pricing([gym_id])
        offer_map = await self.dp_repo.fetch_offer_flags([gym_id])
        promo_counts = await self.dp_repo.fetch_promo_counts([gym_id])

        return self.resolve_price(
            gym_id, pricing_map, offer_map, promo_counts,
            user_dp_eligible=user_dp_eligible,
        )

    # ── Pricing breakdown (actual + offer) for head_count ──

    async def get_gym_pricing_breakdown(
        self,
        gym_id: int,
        user_dp_eligible: bool = False,
    ) -> Tuple[Optional[int], Optional[int], bool]:
        """Return (actual_price, offer_price, offer_active) for one gym.

        actual_price:  dynamic gym price (no intro offer).
        offer_price:   ₹49 if user+gym qualifies, else same as actual_price.
        offer_active:  True when user is getting intro-offer pricing.
        """
        pricing_map = await self.dp_repo.fetch_dailypass_pricing([gym_id])
        offer_map = await self.dp_repo.fetch_offer_flags([gym_id])
        promo_counts = await self.dp_repo.fetch_promo_counts([gym_id])

        actual_price = self.resolve_price(
            gym_id, pricing_map, offer_map, promo_counts,
            user_dp_eligible=False,
        )
        if actual_price is None:
            return None, None, False

        if not user_dp_eligible:
            return actual_price, actual_price, False

        offer_price = self.resolve_price(
            gym_id, pricing_map, offer_map, promo_counts,
            user_dp_eligible=True,
        )
        offer_active = offer_price != actual_price
        return actual_price, (offer_price if offer_active else actual_price), offer_active

    # ── Core resolution (shared logic) ───────────────────────

    @staticmethod
    def resolve_price(
        gym_id: int,
        pricing_map: dict,
        offer_map: dict,
        promo_counts: dict,
        user_dp_eligible: bool = False,
        force_offer_price: bool = False,
    ) -> Optional[int]:
        """Determine dailypass price in rupees: max(owner_base, 90) × markup.

        No intro/gym offer. offer_map / promo_counts / user_dp_eligible /
        force_offer_price are accepted for signature compatibility but no longer
        affect the price. Shared with the v1 dailypass processor via
        compute_dailypass_price_rupees so display == charge.
        """
        pricing = pricing_map.get(gym_id)
        if not pricing or not pricing.discount_price:
            return None

        return compute_dailypass_price_rupees(pricing.discount_price)

    # ── Bulk price map (used by daily_pass list sorting) ─────

    async def build_price_sort_map(
        self, gym_ids: List[int], user_dp_eligible: bool = False
    ) -> Dict[int, float]:
        """Build {gym_id: display_price} for sorting."""

        pipe = self.redis.pipeline(transaction=False)
        for gid in gym_ids:
            pipe.hget(DAILYPASS_HASH_KEY, str(gid))
        raw_results = await pipe.execute()

        price_map: Dict[int, float] = {}
        for gid, raw in zip(gym_ids, raw_results):
            if raw is None:
                continue
            discount_price_paisa = int(raw)
            if not discount_price_paisa:
                continue

            price_map[gid] = compute_dailypass_price_rupees(discount_price_paisa)

        return price_map
