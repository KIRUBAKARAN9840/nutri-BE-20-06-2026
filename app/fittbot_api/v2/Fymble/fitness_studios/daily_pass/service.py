"""Business logic for Daily Pass gym listing.

Orchestrates shared services + dailypass-specific repository.
"""

import asyncio
import json
from typing import Dict, List, Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_sessionmaker

from sqlalchemy import select

from app.models.fittbot_models import Gym, GymLocation, GymStudiosPic

from ..shared.base_listing_service import BaseListingService, inject_test_gym
from ..shared.pricing_service import PricingService
from .repository import DailyPassRepository
from .schemas import (
    DailyPassGymResponse,
    DailyPassListParams,
    DailyPassListResponse,
    GymAddress,
    GymDetailsResponse,
    GymPhotoItem,
)


class DailyPassService(BaseListingService):
    """Dailypass gym listing: candidate resolution → filter → sort → paginate → enrich."""

    _error_code_prefix = "DP"

    def __init__(self, db: AsyncSession, redis: Redis):
        super().__init__(db, redis)
        self.dp_repo = DailyPassRepository(db, redis)
        self.pricing = PricingService(db, redis)

    async def list_gyms(self, params: DailyPassListParams) -> DailyPassListResponse:

        # Resolve client location (cache or fallback — same as v1)
        client_lat, client_lng = await self._resolve_client_location(
            params.client_id, params.client_lat, params.client_lng
        )

        # Hydrate caches (same as v1 — lock-guarded, skips if fresh)
        AsyncSessionLocal = get_async_sessionmaker()

        async def _hydrate_dailypass():
            async with AsyncSessionLocal() as session:
                dp_repo = DailyPassRepository(session, self.redis)
                await dp_repo.hydrate()

        await asyncio.gather(self._hydrate_geo(), _hydrate_dailypass())

        verified_ids, dp_enabled_ids = await asyncio.gather(
            self.gym_repo.get_verified_gym_ids(),
            self.dp_repo.get_dailypass_enabled_gym_ids(),
        )
        candidate_ids = verified_ids & dp_enabled_ids

        if not candidate_ids:
            return self._empty_response(params)

        # 2. Apply dailypass_low filter (only gyms with offer + under 50 cap)
        if params.dailypass_low:
            low_ids = await self.dp_repo.get_dailypass_low_gym_ids(candidate_ids)
            candidate_ids = candidate_ids & low_ids

        if not candidate_ids:
            return self._empty_response(params)

        # 3. Apply fitness_types filter (same as v1 — Gym.fitness_type JSON column)
        candidate_ids = await self._apply_fitness_filter(candidate_ids, params.fitness_types)

        if not candidate_ids:
            return self._empty_response(params)

        # 4. Apply text filters
        has_filters = any([params.search, params.city, params.area, params.pincode, params.state, params.fitness_types, params.dailypass_low])
        candidate_ids = await self._apply_text_filters(
            candidate_ids,
            search=params.search,
            city=params.city,
            area=params.area,
            pincode=params.pincode,
            state=params.state,
        )

        if not candidate_ids:
            return self._empty_response(params)

        candidate_ids = inject_test_gym(candidate_ids, params.client_id)

        # 3. User offer eligibility (needed for both price sort and response)
        user_offer = await self.dp_repo.get_user_offer_eligibility(params.client_id)

        # 4. Sort by distance (shared geo service) — uses resolved lat/lng (with cache fallback)
        ordered_ids, distance_map = await self.geo.resolve_ordered_ids(
            candidate_ids, client_lat, client_lng, has_filters
        )

        if params.client_id == 508 and 1 not in ordered_ids:
            ordered_ids = [1] + list(ordered_ids)

        # 5. Price-based re-sorting (same as v1 _build_price_sort_map, dailypass only)
        #    Tiebreak: same price → nearest first. Null distance → end.
        if params.sort_price and ordered_ids:
            price_map = await self.pricing.build_price_sort_map(
                ordered_ids, user_dp_eligible=user_offer["dailypass_offer_eligible"]
            )
            ordered_ids = self._price_sort(
                ordered_ids, price_map, distance_map,
                descending=params.sort_type == "descending",
            )

        # 6. Paginate
        page_ids, total_count, total_pages = self._paginate(
            ordered_ids, params.page, params.limit
        )

        if not page_ids:
            return self._empty_response(params, total_count=total_count)

        # 7. Bulk fetch & build response
        gym_data = await self._build_responses(
            page_ids, distance_map,
            user_dp_eligible=user_offer["dailypass_offer_eligible"],
            force_offer_price=params.dailypass_low,
        )

        # 8. Return
        return DailyPassListResponse(
            data=gym_data,
            dailypass_offer_eligible=user_offer["dailypass_offer_eligible"],
            dailypass_count=user_offer["dailypass_count"],
            client_name=user_offer["client_name"],
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages
            ),
        )

    async def get_gym_details(self, gym_id: int) -> Optional[GymDetailsResponse]:
        """Fetch gym details: name, address, lat/lng, operating hours, services."""

        result = await self.db.execute(
            select(Gym).where(Gym.gym_id == gym_id)
        )
        gym = result.scalars().first()
        if not gym:
            return None

        location = await self.db.execute(
            select(GymLocation).where(GymLocation.gym_id == gym_id)
        )
        
        loc = location.scalars().first()

        photos_result = await self.db.execute(
            select(GymStudiosPic).where(GymStudiosPic.gym_id == gym_id)
        )
        photos = photos_result.scalars().all()


        return GymDetailsResponse(
            gym_id=gym_id,
            gym_name=gym.name.upper() if gym.name else None,
            address=GymAddress(
                door_no=gym.door_no,
                building=gym.building,
                street=gym.street,
                area=gym.area,
                city=gym.city,
                state=gym.state,
                pincode=gym.pincode,
            ),
            latitude=float(loc.latitude) if loc and loc.latitude else None,
            longitude=float(loc.longitude) if loc and loc.longitude else None,
            operating_hours=json.loads(gym.operating_hours) if isinstance(gym.operating_hours, str) else gym.operating_hours,
            services=json.loads(gym.services) if isinstance(gym.services, str) else gym.services,
            gym_pics=[
                GymPhotoItem(
                    photo_id=p.photo_id,
                    type=p.type,
                    image_url=p.image_url,
                ) for p in photos
            ],
        )

    async def _build_responses(
        self, gym_ids: List[int], distance_map: Dict[int, float],
        user_dp_eligible: bool = False, force_offer_price: bool = False,
    ) -> List[DailyPassGymResponse]:
        """Parallel bulk fetch from shared + domain repos, then assemble DTOs."""

        gyms_map, cover_pics, views_map, freq_booked_set = (
            await self._fetch_gym_data(gym_ids)
        )
        pricing_map = await self.dp_repo.fetch_dailypass_pricing(gym_ids)
        offer_map = await self.dp_repo.fetch_offer_flags(gym_ids)
        promo_counts = await self.dp_repo.fetch_promo_counts(gym_ids)

        results: List[DailyPassGymResponse] = []

        for gid in gym_ids:
            gym = gyms_map.get(gid)
            if not gym:
                continue

            dailypass_price = PricingService.resolve_price(
                gid, pricing_map, offer_map, promo_counts,
                user_dp_eligible=user_dp_eligible,
                force_offer_price=force_offer_price,
            )
            distance = distance_map.get(gid)

            results.append(
                DailyPassGymResponse(
                    gym_id=gid,
                    gym_name=gym.name.upper() if gym.name else None,
                    cover_pic=cover_pics.get(gid, ""),
                    area=gym.area,
                    distance_km=round(distance, 2) if distance is not None else None,
                    views=views_map.get(gid, 0),
                    frequently_booked=gid in freq_booked_set,
                    dailypass_price=dailypass_price,
                )
            )

        return results

    def _empty_response(self, params: DailyPassListParams, total_count: int = 0) -> DailyPassListResponse:
        total_pages = max(1, (total_count + params.limit - 1) // params.limit) if total_count else 0
        return DailyPassListResponse(
            data=[],
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages
            ),
        )
