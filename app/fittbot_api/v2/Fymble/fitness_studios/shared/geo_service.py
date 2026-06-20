"""Shared geo/distance service used by daily_pass, sessions, gym_membership.

Handles Redis GEO hydration, GEOSEARCH, distance sorting, and radius capping.
Same logic as v1 hydrate_verified_gyms_geo.
"""

import asyncio
from typing import Dict, List, Optional, Set, Tuple

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog

from app.models.fittbot_models import Gym, GymLocation

GEO_KEY = "geo:gyms:verified"
GEO_REFRESH_KEY = "geo:gyms:verified:last_refresh"
VERIFIED_SET_KEY = "set:verified_gyms"
GEO_TTL_SECONDS = 3 * 60 * 60  # 3 hours

DEFAULT_RADIUS_KM = 10.0
FILTER_RADIUS_KM = 200.0


class GeoService:
    """Location-based gym discovery via Redis GEO index."""

    def __init__(self, redis: Redis):
        self.redis = redis

    # ── Hydration (same as v1 hydrate_verified_gyms_geo) ─────────

    async def hydrate(self, db: AsyncSession) -> bool:
        """Populate Redis GEO index + verified set from DB. Lock-guarded, TTL-based."""
        lock_key = f"{GEO_REFRESH_KEY}:lock"
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)

        if not acquired:
            exists = await self.redis.exists(GEO_REFRESH_KEY)
            if exists:
                return False
            await asyncio.sleep(0.1)
            exists = await self.redis.exists(GEO_REFRESH_KEY)
            return not exists

        try:
            exists = await self.redis.exists(GEO_REFRESH_KEY)
            if exists:
                await self.redis.delete(lock_key)
                return False

            location_stmt = (
                select(
                    Gym.gym_id,
                    GymLocation.latitude,
                    GymLocation.longitude,
                )
                .join(GymLocation, GymLocation.gym_id == Gym.gym_id)
                .where(
                    Gym.fittbot_verified.is_(True),
                    GymLocation.latitude.isnot(None),
                    GymLocation.longitude.isnot(None),
                )
            )
            result = await db.execute(location_stmt)
            rows = result.all()

            if not rows:
                await self.redis.setex(GEO_REFRESH_KEY, GEO_TTL_SECONDS, "empty")
                await self.redis.delete(lock_key)
                return True

            pipe = self.redis.pipeline()
            pipe.delete(GEO_KEY)
            pipe.delete(VERIFIED_SET_KEY)

            geo_args = []
            verified_ids = []
            for row in rows:
                geo_args.extend([float(row.longitude), float(row.latitude), str(row.gym_id)])
                verified_ids.append(str(row.gym_id))

            if geo_args:
                pipe.execute_command("GEOADD", GEO_KEY, *geo_args)
                pipe.sadd(VERIFIED_SET_KEY, *verified_ids)

            pipe.setex(GEO_REFRESH_KEY, GEO_TTL_SECONDS, str(len(rows)))
            pipe.delete(lock_key)
            await pipe.execute()

            return True

        except RedisError as e:
            jlog("error", {
                "type": "cache_hydration_failure",
                "error_code": "GEO_HYDRATE_REDIS",
                "detail": str(e),
                "exc": repr(e),
            })
            try:
                await self.redis.delete(lock_key)
            except RedisError:
                pass
            return False
        except Exception as e:
            jlog("error", {
                "type": "cache_hydration_failure",
                "error_code": "GEO_HYDRATE_ERROR",
                "detail": str(e),
                "exc": repr(e),
            })
            try:
                await self.redis.delete(lock_key)
            except RedisError:
                pass
            return False

    # ── GEOSEARCH ────────────────────────────────────────────────

    async def get_nearby_distances(
        self, lat: float, lng: float, radius_km: float, count: int = 1000
    ) -> Dict[int, float]:
        """Return {gym_id: distance_km} from Redis GEOSEARCH, sorted ASC."""
        try:
            results = await self.redis.geosearch(
                GEO_KEY,
                longitude=lng,
                latitude=lat,
                radius=radius_km,
                unit="km",
                withdist=True,
                count=count,
                sort="ASC",
            )
            return {
                int(gid.decode() if isinstance(gid, bytes) else gid): float(dist)
                for gid, dist in results
            }
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "GEO_SEARCH_FAILURE",
                "detail": str(e),
                "lat": lat,
                "lng": lng,
                "radius_km": radius_km,
            })
            return {}

    # ── Sorting ──────────────────────────────────────────────────

    def sort_by_distance(
        self,
        candidate_ids: Set[int],
        distance_map: Dict[int, float],
        *,
        has_filters: bool,
        radius_cap_km: float = DEFAULT_RADIUS_KM,
    ) -> List[int]:
        """Sort candidates by distance. Returns ordered gym ID list.

        - Without filters: only include gyms within radius_cap_km
        - With filters: include all + append gyms without location at end
        """
        ordered = [
            gid
            for gid in sorted(distance_map, key=distance_map.get)
            if gid in candidate_ids
        ]

        if not has_filters:
            return [gid for gid in ordered if distance_map.get(gid, 999) <= radius_cap_km]

        with_loc = set(distance_map.keys())
        without_loc = sorted(gid for gid in candidate_ids if gid not in with_loc)
        return ordered + without_loc

    async def resolve_ordered_ids(
        self,
        candidate_ids: Set[int],
        lat: Optional[float],
        lng: Optional[float],
        has_filters: bool,
    ) -> Tuple[List[int], Dict[int, float]]:
        """Full flow: fetch distances + sort. Returns (ordered_ids, distance_map)."""
        if lat is None or lng is None:
            return sorted(candidate_ids), {}

        radius = FILTER_RADIUS_KM if has_filters else DEFAULT_RADIUS_KM
        distance_map = await self.get_nearby_distances(lat, lng, radius)

        ordered = self.sort_by_distance(
            candidate_ids, distance_map, has_filters=has_filters
        )
        return ordered, distance_map
