"""Shared gym data queries used across all listing domains.

Verified gym IDs, gym rows, cover pics, text search.
"""

from typing import Dict, List, Optional, Set

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog

from app.models.fittbot_models import Gym, GymStudiosPic

VERIFIED_SET_KEY = "set:verified_gyms"


class GymRepository:
    """Common gym queries shared by daily_pass, sessions, membership."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def get_verified_gym_ids(self) -> Set[int]:
        """Verified gym IDs from Redis set, DB fallback."""
        try:
            members = await self.redis.smembers(VERIFIED_SET_KEY)
            if members:
                return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "VERIFIED_GYMS_CACHE_MISS",
                "detail": str(e),
                "fallback": "database",
            })

        result = await self.db.execute(
            select(Gym.gym_id).where(Gym.fittbot_verified.is_(True))
        )
        return {r[0] for r in result.all()}

    async def get_all_gym_ids(self) -> Set[int]:
        """All gym IDs regardless of fittbot_verified."""
        result = await self.db.execute(select(Gym.gym_id))
        return {r[0] for r in result.all()}

    async def fetch_gyms(self, gym_ids: List[int]) -> Dict[int, Gym]:
        """Fetch Gym rows keyed by gym_id."""
        if not gym_ids:
            return {}
        result = await self.db.execute(select(Gym).where(Gym.gym_id.in_(gym_ids)))
        return {g.gym_id: g for g in result.scalars().all()}

    async def fetch_cover_pics(self, gym_ids: List[int]) -> Dict[int, str]:
        """Fetch cover_pic URLs keyed by gym_id."""
        if not gym_ids:
            return {}
        stmt = select(GymStudiosPic).where(
            GymStudiosPic.gym_id.in_(gym_ids),
            GymStudiosPic.type == "cover_pic",
        )
        result = await self.db.execute(stmt)
        return {cp.gym_id: cp.image_url for cp in result.scalars().all()}

    async def search_gym_ids(
        self,
        *,
        search: Optional[str] = None,
        city: Optional[str] = None,
        area: Optional[str] = None,
        pincode: Optional[str] = None,
        state: Optional[str] = None,
        include_unverified: bool = False,
    ) -> Set[int]:
        """Return gym IDs matching text filters."""
        filters = [] if include_unverified else [Gym.fittbot_verified.is_(True)]

        if search:
            term = f"%{search}%"
            filters.append(
                or_(
                    Gym.name.ilike(term),
                    Gym.location.ilike(term),
                    Gym.area.ilike(term),
                    Gym.city.ilike(term),
                    Gym.state.ilike(term),
                    Gym.pincode.ilike(term),
                )
            )
        if city:
            filters.append(Gym.city.ilike(f"%{city}%"))
        if area:
            filters.append(Gym.area.ilike(f"%{area}%"))
        if state:
            filters.append(Gym.state.ilike(f"%{state}%"))
        if pincode:
            filters.append(Gym.pincode == pincode)

        result = await self.db.execute(select(Gym.gym_id).where(*filters))
        return {r[0] for r in result.all()}

    async def filter_by_fitness_types(
        self, fitness_types: List[str], verified_ids: Set[int],
        include_unverified: bool = False,
    ) -> Set[int]:
        """Gym IDs whose fitness_type JSON column contains any of the requested types."""
        ft_conditions = [Gym.fitness_type.like(f'%"{ft}"%') for ft in fitness_types]
        where_clauses = [or_(*ft_conditions), Gym.gym_id.in_(verified_ids)]
        if not include_unverified:
            where_clauses.append(Gym.fittbot_verified.is_(True))
        stmt = select(Gym.gym_id).where(*where_clauses)
        result = await self.db.execute(stmt)
        return {r[0] for r in result.all()}
