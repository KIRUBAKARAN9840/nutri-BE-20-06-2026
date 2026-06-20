"""Shared gym stats: views & frequently_booked.

Used by daily_pass, sessions, gym_membership listing domains.
Redis-first with DB fallback + cache-back.
"""

from typing import Dict, List, Set

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog

from app.models.client_activity_models import ClientActivitySummary
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem

# Redis keys (same as v1 — shared cache)
GYM_VIEWS_KEY = "gym_views:{gym_id}"
GYM_VIEWS_TTL = 10 * 60  # 10 minutes
GYM_FREQ_BOOKED_KEY = "gym_freq_booked:{gym_id}"
GYM_FREQ_BOOKED_TTL = 24 * 60 * 60  # 24 hours


class GymStatsService:
    """Fetches views and frequently_booked for a batch of gyms."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def fetch_views(self, gym_ids: List[int]) -> Dict[int, int]:
        """Return {gym_id: view_count}. Redis pipeline → DB fallback → cache back."""
        if not gym_ids:
            return {}

        views_map: Dict[int, int] = {}
        miss_ids: List[int] = []

        # Redis pipeline
        try:
            pipe = self.redis.pipeline(transaction=False)
            for gid in gym_ids:
                pipe.get(GYM_VIEWS_KEY.format(gym_id=gid))
            raw_values = await pipe.execute()
            for gid, raw in zip(gym_ids, raw_values):
                if raw is not None:
                    views_map[gid] = int(raw)
                else:
                    miss_ids.append(gid)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "GYM_VIEWS_CACHE_READ",
                "detail": str(e),
                "fallback": "database",
                "gym_count": len(gym_ids),
            })
            miss_ids = list(gym_ids)

        # DB fallback
        if miss_ids:
            stmt = (
                select(
                    ClientActivitySummary.gym_id,
                    func.sum(ClientActivitySummary.total_views).label("views"),
                )
                .where(ClientActivitySummary.gym_id.in_(miss_ids))
                .group_by(ClientActivitySummary.gym_id)
            )
            result = await self.db.execute(stmt)
            db_views = {row.gym_id: int(row.views) for row in result.all()}
            views_map.update(db_views)

            # Cache back (fire-and-forget)
            try:
                cache_pipe = self.redis.pipeline(transaction=False)
                for gid in miss_ids:
                    cache_pipe.setex(
                        GYM_VIEWS_KEY.format(gym_id=gid),
                        GYM_VIEWS_TTL,
                        str(db_views.get(gid, 0)),
                    )
                await cache_pipe.execute()
            except RedisError as e:
                jlog("warning", {
                    "type": "cache_write_failure",
                    "error_code": "GYM_VIEWS_CACHE_WRITE",
                    "detail": str(e),
                    "gym_count": len(miss_ids),
                })

        return views_map

    async def fetch_frequently_booked(self, gym_ids: List[int]) -> Set[int]:
        """Return set of gym_ids that have at least one paid order."""
        if not gym_ids:
            return set()

        booked: Set[int] = set()
        miss_ids: List[int] = []

        # Redis pipeline
        try:
            pipe = self.redis.pipeline(transaction=False)
            for gid in gym_ids:
                pipe.get(GYM_FREQ_BOOKED_KEY.format(gym_id=gid))
            raw_values = await pipe.execute()
            for gid, raw in zip(gym_ids, raw_values):
                if raw is not None:
                    if raw == b"1" or raw == "1":
                        booked.add(gid)
                else:
                    miss_ids.append(gid)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "GYM_FREQ_BOOKED_CACHE_READ",
                "detail": str(e),
                "fallback": "database",
                "gym_count": len(gym_ids),
            })
            miss_ids = list(gym_ids)

        # DB fallback
        if miss_ids:
            stmt = (
                select(OrderItem.gym_id)
                .join(Order, Order.id == OrderItem.order_id)
                .where(
                    OrderItem.gym_id.in_([str(gid) for gid in miss_ids]),
                    Order.status == "paid",
                )
                .distinct()
            )
            result = await self.db.execute(stmt)
            db_freq = {int(row[0]) for row in result.all()}
            booked.update(db_freq)

            # Cache back (fire-and-forget)
            try:
                cache_pipe = self.redis.pipeline(transaction=False)
                for gid in miss_ids:
                    cache_pipe.setex(
                        GYM_FREQ_BOOKED_KEY.format(gym_id=gid),
                        GYM_FREQ_BOOKED_TTL,
                        "1" if gid in db_freq else "0",
                    )
                await cache_pipe.execute()
            except RedisError as e:
                jlog("warning", {
                    "type": "cache_write_failure",
                    "error_code": "GYM_FREQ_BOOKED_CACHE_WRITE",
                    "detail": str(e),
                    "gym_count": len(miss_ids),
                })

        return booked
