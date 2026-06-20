"""Database & cache queries specific to Diet macros/micros.

Only diet-specific data access lives here.
"""

import json
from datetime import date, datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import ClientActual, ClientTarget, ActualDiet
from app.models.fittbot_models.food_nutrition import AiDietCoachFood
from app.fittbot_api.v1.payments.models.credits import CreditBalance
from app.utils.logging_setup import jlog

TARGET_ACTUAL_TTL = 86400  # 24 hours


class DietRepository:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def get_cached_target_actual(self, client_id: int) -> Optional[dict]:
        try:
            data = await self.redis.get(f"{client_id}:target_actual:{date.today().isoformat()}")
            if data:
                return json.loads(data)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "DIET_TARGET_ACTUAL_CACHE_READ",
                "detail": str(e),
                "client_id": client_id,
            })
        return None

    async def cache_target_actual(self, client_id: int, data: dict) -> None:
        try:
            key = f"{client_id}:target_actual:{date.today().isoformat()}"
            await self.redis.set(key, json.dumps(data))
            await self.redis.expire(key, TARGET_ACTUAL_TTL)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_write_failure",
                "error_code": "DIET_TARGET_ACTUAL_CACHE_WRITE",
                "detail": str(e),
                "client_id": client_id,
            })

    async def get_client_target(self, client_id: int) -> Optional[ClientTarget]:
        result = await self.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        return result.scalars().first()

    async def get_actual_diet(self, client_id: int) -> Optional[ActualDiet]:
        result = await self.db.execute(
            select(ActualDiet).where(
                ActualDiet.client_id == client_id, ActualDiet.date == date.today()
            )
        )
        return result.scalars().first()

    async def get_client_actual(self, client_id: int) -> Optional[ClientActual]:
        result = await self.db.execute(
            select(ClientActual).where(
                ClientActual.client_id == client_id, ClientActual.date == date.today()
            )
        )
        return result.scalars().first()

    async def fetch_credit_balance(self, client_id: int) -> int:
        # Sweep any expired grants in an isolated session — safe inside the
        # asyncio.gather context this method is called from.
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service_async import (
            expire_stale_credits_isolated,
        )
        await expire_stale_credits_isolated(client_id, redis=self.redis)

        stmt = select(CreditBalance.balance).where(
            CreditBalance.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        row = result.scalar()
        return row if row is not None else 0

    async def fetch_credit_balance_and_unlimited(self, client_id: int):
        """Return (balance, is_unlimited) in a single query.

        is_unlimited = active credit_999 unlimited-scan pass. Sweeps expired
        grants first (isolated session — safe inside asyncio.gather).
        """
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service_async import (
            expire_stale_credits_isolated,
        )
        await expire_stale_credits_isolated(client_id, redis=self.redis)

        stmt = select(
            CreditBalance.balance, CreditBalance.unlimited_until
        ).where(CreditBalance.client_id == client_id)
        result = await self.db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return 0, False

        balance, unlimited_until = row
        is_unlimited = False
        if unlimited_until is not None:
            # Compare tz-naive to avoid naive/aware mismatch on DB datetimes.
            now_naive = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
            uu_naive = (
                unlimited_until.replace(tzinfo=None)
                if unlimited_until.tzinfo else unlimited_until
            )
            is_unlimited = uu_naive > now_naive
        return (balance or 0), is_unlimited

    async def upsert_client_target(self, client_id: int, values: dict) -> ClientTarget:
        target = await self.get_client_target(client_id)
        if target:
            for key, val in values.items():
                setattr(target, key, val)
        else:
            target = ClientTarget(client_id=client_id, **values)
            self.db.add(target)
        await self.db.commit()
        await self.db.refresh(target)
        return target

    async def invalidate_target_actual_cache(self, client_id: int) -> None:
        try:
            await self.redis.delete(f"{client_id}:target_actual:{date.today().isoformat()}")
        except RedisError as e:
            jlog("warning", {
                "type": "cache_invalidate_failure",
                "error_code": "DIET_TARGET_ACTUAL_CACHE_INVALIDATE",
                "detail": str(e),
                "client_id": client_id,
            })

    async def get_foods_by_preference(self, preference: str):
        result = await self.db.execute(
            select(AiDietCoachFood)
            .where(AiDietCoachFood.img_type == preference)
            .order_by(AiDietCoachFood.id)
        )
        return result.scalars().all()

    async def invalidate_report_info_cache(self, client_id: int) -> None:
        try:
            await self.redis.delete(f"{client_id}:report_info")
        except RedisError as e:
            jlog("warning", {
                "type": "cache_invalidate_failure",
                "error_code": "DIET_REPORT_INFO_CACHE_INVALIDATE",
                "detail": str(e),
                "client_id": client_id,
            })
