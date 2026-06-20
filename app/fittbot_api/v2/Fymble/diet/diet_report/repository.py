"""Database & cache queries for Diet Report.

Client profile + target cached together in one Redis key.
"""

import json
from typing import Dict, List, Optional

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import ActualDiet, Client, ClientTarget
from app.utils.logging_setup import jlog

CLIENT_REPORT_INFO_TTL = 86400  # 24 hours


class DietReportRepository:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # ── Actual Diet ──

    async def get_all_actual_diets(self, client_id: int) -> List[ActualDiet]:
        result = await self.db.execute(
            select(ActualDiet)
            .where(ActualDiet.client_id == client_id)
            .order_by(ActualDiet.date.desc())
        )
        return result.scalars().all()

    # ── Client Profile + Target (single Redis key) ──

    async def get_client_report_info(self, client_id: int) -> Dict:
        """Return client profile + target from cache or DB.

        Cached as one key: {client_id}:report_info
        """
        cached = await self._get_cached_report_info(client_id)
        if cached:
            return cached

        profile, target = await self._fetch_profile_and_target(client_id)

        data = {
            "profile": {
                "weight": profile.weight if profile else None,
                "height": profile.height if profile else None,
                "age": profile.age if profile else None,
                "gender": profile.gender if profile else None,
                "goals": profile.goals if profile else None,
                "lifestyle": profile.lifestyle if profile else None,
            },
            "target": {
                "calories": target.calories if target else None,
                "protein": target.protein if target else None,
                "carbs": target.carbs if target else None,
                "fat": target.fat if target else None,
                "fiber": target.fiber if target else None,
                "sugar": target.sugar if target else None,
            },
        }

        await self._cache_report_info(client_id, data)
        return data

    async def _fetch_profile_and_target(self, client_id: int):
        profile_result = await self.db.execute(
            select(Client).where(Client.client_id == client_id)
        )
        target_result = await self.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        return profile_result.scalars().first(), target_result.scalars().first()

    async def _get_cached_report_info(self, client_id: int) -> Optional[Dict]:
        try:
            data = await self.redis.get(f"{client_id}:report_info")
            if data:
                return json.loads(data)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "DIET_REPORT_INFO_CACHE_READ",
                "detail": str(e),
                "client_id": client_id,
            })
        return None

    async def _cache_report_info(self, client_id: int, data: Dict) -> None:
        try:
            key = f"{client_id}:report_info"
            await self.redis.set(key, json.dumps(data))
            await self.redis.expire(key, CLIENT_REPORT_INFO_TTL)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_write_failure",
                "error_code": "DIET_REPORT_INFO_CACHE_WRITE",
                "detail": str(e),
                "client_id": client_id,
            })
