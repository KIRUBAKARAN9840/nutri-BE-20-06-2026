"""Reusable session pricing resolution.

Extracts the price-calculation logic so it can be shared across
sessions (listing) and session_bookings (checkout preview).
Mirrors PricingService for dailypass.
"""

import asyncio
from typing import Dict, List, Optional, Tuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.constants import GYM_OFFER_USER_CAP
from app.config.pricing import get_markup_multiplier, compute_session_price_rupees

from app.fittbot_api.v2.Fymble.fitness_studios.sessions.repository import (
    SESSION_OFFER_PRICE,
    SessionRepository,
)


class SessionPricingService:
    """Resolve the display price for a session gym."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.sess_repo = SessionRepository(db, redis)

    # ── Single-gym pricing breakdown (used by session_bookings) ──

    async def get_gym_pricing_breakdown(
        self,
        gym_id: int,
        session_id: int,
        client_id: int,
        user_sess_eligible: bool = False,
    ) -> Tuple[Optional[int], Optional[int], bool]:
        """Return (actual_price, offer_price, offer_active) for one gym+session.

        actual_price:  dynamic session price (no intro offer).
        offer_price:   ₹99 if user+gym qualifies, else same as actual_price.
        offer_active:  True when user is getting intro-offer pricing.
        """
        settings_map = await self.sess_repo.fetch_session_settings([gym_id], session_id)
        offer_map = await self.sess_repo.fetch_offer_flags([gym_id])
        promo_counts = await self.sess_repo.fetch_promo_counts([gym_id])
        booked_gyms = await self.sess_repo.fetch_user_booked_promo_gyms(client_id, [gym_id])

        setting = settings_map.get(gym_id)
        if not setting or not setting.final_price:
            return None, None, False

        actual_price = compute_session_price_rupees(setting.final_price)

        # Intro/gym offer removed — price is always max(final_price, ₹90) × markup.
        return actual_price, actual_price, False

    # ── Core offer check (shared logic) ──────────────────────

    @staticmethod
    def is_offer_active(
        gym_id: int,
        offer_map: dict,
        promo_counts: dict,
        booked_gyms: set,
        user_sess_eligible: bool = False,
        force_offer_price: bool = False,
    ) -> bool:
        """Intro/gym session offer removed — always inactive.

        Kept for signature compatibility; price is now owner final_price × markup.
        """
        return False

    # ── Bulk price map (used by sessions list sorting + display) ──

    async def build_price_sort_map(
        self,
        gym_ids: List[int],
        session_id: int,
        user_sess_eligible: bool = False,
        force_offer_price: bool = False,
        client_id: Optional[int] = None,
    ) -> Dict[int, float]:
        """Build {gym_id: display_price} for session price sorting."""

        settings_map = await self.sess_repo.fetch_session_settings(gym_ids, session_id)
        offer_map = await self.sess_repo.fetch_offer_flags(gym_ids)
        promo_counts = await self.sess_repo.fetch_promo_counts(gym_ids)
        booked_gyms = await self.sess_repo.fetch_user_booked_promo_gyms(client_id, gym_ids)

        price_map: Dict[int, float] = {}
        for gid in gym_ids:
            setting = settings_map.get(gid)
            if not setting or not setting.final_price:
                continue

            price_map[gid] = compute_session_price_rupees(setting.final_price)

        return price_map

    async def resolve_display_price(
        self,
        gym_id: int,
        session_id: int,
        offer_map: dict,
        promo_counts: dict,
        booked_gyms: set,
        user_sess_eligible: bool = False,
        force_offer_price: bool = False,
    ) -> Tuple[Optional[int], bool]:
        """Resolve display price and offer_active for a single gym (used in listing responses).

        Returns (display_price, offer_active).
        """
        offer_active = self.is_offer_active(
            gym_id, offer_map, promo_counts, booked_gyms,
            user_sess_eligible=user_sess_eligible,
            force_offer_price=force_offer_price,
        )
        return SESSION_OFFER_PRICE if offer_active else None, offer_active
