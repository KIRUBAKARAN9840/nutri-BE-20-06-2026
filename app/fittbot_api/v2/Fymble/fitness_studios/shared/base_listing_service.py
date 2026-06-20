"""Base service for gym listing endpoints.

Extracts shared logic (location caching, hydration, filtering, sorting,
pagination) so domain services (DailyPass, Sessions) stay DRY.
No business logic lives here — only reusable infrastructure.
"""

import asyncio
from typing import Dict, List, Optional, Set, Tuple

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog
from app.models.async_database import get_async_sessionmaker

from .geo_service import GeoService
from .gym_repository import GymRepository
from .gym_stats_service import GymStatsService
from .schemas import PaginationMeta

LOCATION_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days

TEST_CLIENT_ID = 508
TEST_GYM_ID = 1


def inject_test_gym(candidate_ids: Set[int], client_id: Optional[int]) -> Set[int]:
    if client_id == TEST_CLIENT_ID:
        return set(candidate_ids) | {TEST_GYM_ID}
    return candidate_ids


class BaseListingService:

    _error_code_prefix: str = ""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.gym_repo = GymRepository(db, redis)
        self.geo = GeoService(redis)
        self.stats = GymStatsService(db, redis)

    # ── Location caching ────────────────────────────────────────

    async def _resolve_client_location(
        self,
        client_id: Optional[int],
        client_lat: Optional[float],
        client_lng: Optional[float],
    ) -> Tuple[Optional[float], Optional[float]]:
        """Cache or retrieve client lat/lng — same logic as v1 location_cache_key."""
        if not client_id:
            return client_lat, client_lng

        cache_key = f"client_location:{client_id}"

        if client_lat is not None and client_lng is not None:
            # Fire-and-forget: cache for next time
            asyncio.create_task(self._cache_location(cache_key, client_lat, client_lng))
        else:
            # Fallback: read from Redis cache
            try:
                cached = await self.redis.hgetall(cache_key)
                if cached:
                    cached_lat = cached.get("lat") or cached.get(b"lat")
                    cached_lng = cached.get("lng") or cached.get(b"lng")
                    if cached_lat and cached_lng:
                        client_lat = float(cached_lat.decode() if isinstance(cached_lat, bytes) else cached_lat)
                        client_lng = float(cached_lng.decode() if isinstance(cached_lng, bytes) else cached_lng)
            except RedisError as e:
                jlog("warning", {
                    "type": "cache_read_failure",
                    "error_code": f"{self._error_code_prefix}_LOCATION_CACHE_READ",
                    "detail": str(e),
                    "client_id": client_id,
                })

        return client_lat, client_lng

    async def _cache_location(self, key: str, lat: float, lng: float):
        """Fire-and-forget client location caching."""
        try:
            await self.redis.hset(key, mapping={"lat": str(lat), "lng": str(lng)})
            await self.redis.expire(key, LOCATION_CACHE_TTL)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_write_failure",
                "error_code": f"{self._error_code_prefix}_LOCATION_CACHE_WRITE",
                "detail": str(e),
                "key": key,
            })

    # ── Hydration helpers ───────────────────────────────────────

    async def _hydrate_geo(self):
        """Hydrate geo cache in a fresh DB session (avoids session conflicts)."""
        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            await self.geo.hydrate(session)

    # ── Shared data fetch ──────────────────────────────────────────

    async def _fetch_gym_data(self, gym_ids: List[int]) -> Tuple[Dict, Dict, Dict, Set]:
        """Fetch gyms, cover_pics, views, freq_booked sequentially (same session)."""
        gyms_map = await self.gym_repo.fetch_gyms(gym_ids)
        cover_pics = await self.gym_repo.fetch_cover_pics(gym_ids)
        views_map = await self.stats.fetch_views(gym_ids)
        freq_booked_set = await self.stats.fetch_frequently_booked(gym_ids)
        return gyms_map, cover_pics, views_map, freq_booked_set

    # ── Filter helpers ──────────────────────────────────────────

    async def _apply_fitness_filter(
        self, candidate_ids: Set[int], fitness_types: Optional[List[str]],
        include_unverified: bool = False,
    ) -> Set[int]:
        """Intersect candidates with gyms matching fitness_types. No-op if None."""
        if not fitness_types:
            return candidate_ids
        ft_ids = await self.gym_repo.filter_by_fitness_types(
            fitness_types, candidate_ids, include_unverified=include_unverified,
        )
        return candidate_ids & ft_ids

    async def _apply_text_filters(
        self,
        candidate_ids: Set[int],
        *,
        search: Optional[str] = None,
        city: Optional[str] = None,
        area: Optional[str] = None,
        pincode: Optional[str] = None,
        state: Optional[str] = None,
        include_unverified: bool = False,
    ) -> Set[int]:
        """Intersect candidates with text-matched gym IDs. No-op if all None."""
        if not (search or city or area or pincode or state):
            return candidate_ids
        text_ids = await self.gym_repo.search_gym_ids(
            search=search, city=city, area=area, pincode=pincode, state=state,
            include_unverified=include_unverified,
        )
        return candidate_ids & text_ids

    # ── Sorting ─────────────────────────────────────────────────

    @staticmethod
    def _price_sort(
        ordered_ids: List[int],
        price_map: Dict[int, float],
        distance_map: Dict[int, float],
        descending: bool,
    ) -> List[int]:
        """Re-sort by price with distance as tiebreak. Null distance → end."""
        return sorted(
            ordered_ids,
            key=lambda gid: (
                -price_map.get(gid, float("inf")) if descending else price_map.get(gid, float("inf")),
                distance_map.get(gid, float("inf")),
            ),
        )

    # ── Pagination ──────────────────────────────────────────────

    @staticmethod
    def _paginate(
        ordered_ids: List[int], page: int, limit: int
    ) -> Tuple[List[int], int, int]:
        """Slice ordered IDs into a page. Returns (page_ids, total_count, total_pages)."""
        total_count = len(ordered_ids)
        offset = (page - 1) * limit
        page_ids = ordered_ids[offset: offset + limit]
        total_pages = (total_count + limit - 1) // limit if total_count else 0
        return page_ids, total_count, total_pages

    @staticmethod
    def _build_pagination_meta(
        page: int, limit: int, total_count: int, total_pages: int
    ) -> PaginationMeta:
        return PaginationMeta(
            current_page=page,
            total_pages=total_pages,
            total_count=total_count,
            has_next=page < total_pages,
            has_prev=page > 1,
            limit=limit,
        )
