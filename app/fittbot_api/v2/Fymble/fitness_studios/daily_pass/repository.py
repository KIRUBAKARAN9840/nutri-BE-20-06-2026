"""Database & cache queries specific to Daily Pass.

Only dailypass-specific data access lives here.
Shared queries (gyms, views, frequently_booked) are in shared/.
Includes hydration logic (same as v1 hydrate_dailypass_cache).
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Dict, List, Optional, Set

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog

from app.models.fittbot_models import Gym, NewOffer, Client
from app.models.dailypass_models import DailyPass, DailyPassDay, DailyPassPricing
from app.config.constants import GYM_OFFER_USER_CAP, OFFER_PRICE_PAISE, OFFER_PRICE_RUPEES

USER_DP_INELIGIBLE_KEY = "user:{client_id}:dp_ineligible"

DAILYPASS_HASH_KEY = "hash:dailypass:pricing"
DAILYPASS_LOW_SET_KEY = "set:dailypass:low49"
DAILYPASS_ENABLED_SET_KEY = "set:dailypass:enabled"
DAILYPASS_REFRESH_KEY = "dailypass:last_refresh"
DAILYPASS_TTL_SECONDS = 3 * 60 * 60  # 3 hours

CACHE_TTL_10MIN = 10 * 60


class DailyPassRepository:
    """Dailypass-specific data access only."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # ── Hydration (same as v1 hydrate_dailypass_cache) ───────────

    async def hydrate(self) -> bool:
        """Populate Redis dailypass pricing hash + enabled set from DB. Lock-guarded."""
        lock_key = f"{DAILYPASS_REFRESH_KEY}:lock"
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)

        if not acquired:
            exists = await self.redis.exists(DAILYPASS_REFRESH_KEY)
            if exists:
                return False
            await asyncio.sleep(0.1)
            return not await self.redis.exists(DAILYPASS_REFRESH_KEY)

        try:
            exists = await self.redis.exists(DAILYPASS_REFRESH_KEY)
            if exists:
                await self.redis.delete(lock_key)
                return False

            pricing_stmt = (
                select(
                    DailyPassPricing.gym_id,
                    DailyPassPricing.discount_price,
                    Gym.dailypass,
                    Gym.fittbot_verified,
                )
                .join(Gym, func.cast(Gym.gym_id, String) == DailyPassPricing.gym_id)
                .where(Gym.fittbot_verified.is_(True))
            )
            result = await self.db.execute(pricing_stmt)
            rows = result.all()

            pipe = self.redis.pipeline()
            pipe.delete(DAILYPASS_HASH_KEY)
            pipe.delete(DAILYPASS_LOW_SET_KEY)
            pipe.delete(DAILYPASS_ENABLED_SET_KEY)

            pricing_data = {}
            low_49_ids = []
            enabled_ids = []

            for row in rows:
                gym_id = str(row.gym_id)
                pricing_data[gym_id] = str(row.discount_price or 0)

                if row.dailypass:
                    enabled_ids.append(gym_id)
                    if row.discount_price == OFFER_PRICE_PAISE:
                        low_49_ids.append(gym_id)

            if pricing_data:
                pipe.hset(DAILYPASS_HASH_KEY, mapping=pricing_data)
            if low_49_ids:
                pipe.sadd(DAILYPASS_LOW_SET_KEY, *low_49_ids)
            if enabled_ids:
                pipe.sadd(DAILYPASS_ENABLED_SET_KEY, *enabled_ids)

            pipe.setex(DAILYPASS_REFRESH_KEY, DAILYPASS_TTL_SECONDS, str(len(rows)))
            pipe.delete(lock_key)
            await pipe.execute()
            return True

        except RedisError as e:
            jlog("error", {
                "type": "cache_hydration_failure",
                "error_code": "DAILYPASS_HYDRATE_REDIS",
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
                "error_code": "DAILYPASS_HYDRATE_ERROR",
                "detail": str(e),
                "exc": repr(e),
            })
            try:
                await self.redis.delete(lock_key)
            except RedisError:
                pass
            return False

    # ── User offer eligibility (same as v1 get_user_offer_eligibility) ──

    async def get_user_offer_eligibility(self, client_id: Optional[int]) -> Dict:
        """Return client_name + (no-op) offer eligibility.

        The ₹49 intro offer was removed: pricing is now owner_base floored at ₹90
        plus commission for everyone. So we no longer query the booking count or
        read/write the `dp_ineligible` Redis key — eligibility is always False.
        client_name is still fetched because the response/UX uses it.
        """
        if not client_id:
            return {
                "dailypass_count": 0,
                "dailypass_offer_eligible": False,
                "client_name": None,
            }

        client_stmt = select(Client.name).where(Client.client_id == client_id)
        client_result = await self.db.execute(client_stmt)
        client_name = client_result.scalar()

        return {
            "dailypass_count": 0,
            "dailypass_offer_eligible": False,
            "client_name": client_name,
        }

    # ── Queries ───────────────────────────────────────────────────

    async def get_dailypass_enabled_gym_ids(self) -> Set[int]:
        """Gym IDs that have dailypass pricing. Redis first, DB fallback."""
        try:
            members = await self.redis.smembers(DAILYPASS_ENABLED_SET_KEY)
            if members:
                return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "DP_ENABLED_CACHE_MISS",
                "detail": str(e),
                "fallback": "database",
            })

        stmt = (
            select(cast(DailyPassPricing.gym_id, String))
            .join(Gym, cast(Gym.gym_id, String) == DailyPassPricing.gym_id)
            .where(Gym.fittbot_verified.is_(True), Gym.dailypass.is_(True))
        )
        result = await self.db.execute(stmt)
        return {int(r[0]) for r in result.all()}

    async def get_dailypass_low_gym_ids(self, candidate_ids: Set[int]) -> Set[int]:
        """Gym IDs eligible for ₹49 offer: new_offer.dailypass=true + promo count < 50."""
        if not candidate_ids:
            return set()

        offer_map = await self.fetch_offer_flags(list(candidate_ids))
        promo_counts = await self.fetch_promo_counts(list(candidate_ids))

        return {
            gid for gid in candidate_ids
            if (offer_map.get(gid) and offer_map[gid].dailypass
                and promo_counts.get(gid, 0) < GYM_OFFER_USER_CAP)
        }

    async def fetch_dailypass_pricing(self, gym_ids: List[int]) -> Dict[int, DailyPassPricing]:
        """Fetch DailyPassPricing records keyed by gym_id (int). Cached 10 min per gym."""
        if not gym_ids:
            return {}

        cache_keys = [f"dp:pricing:{gid}" for gid in gym_ids]
        result_map = {}
        uncached_ids = []

        try:
            cached_values = await self.redis.mget(cache_keys)
            for gid, val in zip(gym_ids, cached_values):
                if val is not None:
                    raw = val.decode() if isinstance(val, bytes) else val
                    result_map[gid] = SimpleNamespace(**json.loads(raw))
                else:
                    uncached_ids.append(gid)
        except (RedisError, json.JSONDecodeError):
            uncached_ids = list(gym_ids)

        if not uncached_ids:
            return result_map

        stmt = select(DailyPassPricing).where(
            DailyPassPricing.gym_id.in_([str(gid) for gid in uncached_ids])
        )
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        for p in rows:
            result_map[int(p.gym_id)] = p

        try:
            pipe = self.redis.pipeline()
            for p in rows:
                pipe.setex(
                    f"dp:pricing:{int(p.gym_id)}", CACHE_TTL_10MIN,
                    json.dumps({"gym_id": p.gym_id, "discount_price": p.discount_price}),
                )
            await pipe.execute()
        except RedisError:
            pass

        return result_map

    async def fetch_offer_flags(self, gym_ids: List[int]) -> Dict[int, NewOffer]:
        """Fetch NewOffer rows keyed by gym_id. Cached 10 min per gym."""
        if not gym_ids:
            return {}

        cache_keys = [f"dp:offer_flags:{gid}" for gid in gym_ids]
        result_map = {}
        uncached_ids = []

        try:
            cached_values = await self.redis.mget(cache_keys)
            for gid, val in zip(gym_ids, cached_values):
                if val is not None:
                    raw = val.decode() if isinstance(val, bytes) else val
                    result_map[gid] = SimpleNamespace(**json.loads(raw))
                else:
                    uncached_ids.append(gid)
        except (RedisError, json.JSONDecodeError):
            uncached_ids = list(gym_ids)

        if not uncached_ids:
            return result_map

        stmt = select(NewOffer).where(NewOffer.gym_id.in_(uncached_ids))
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        for row in rows:
            result_map[row.gym_id] = row

        try:
            pipe = self.redis.pipeline()
            for row in rows:
                pipe.setex(
                    f"dp:offer_flags:{row.gym_id}", CACHE_TTL_10MIN,
                    json.dumps({"gym_id": row.gym_id, "dailypass": bool(row.dailypass)}),
                )
            await pipe.execute()
        except RedisError:
            pass

        return result_map

    async def fetch_promo_counts(self, gym_ids: List[int]) -> Dict[int, int]:
        """Count unique users who booked at ₹49 offer price per gym. Cached 10 min per gym."""
        if not gym_ids:
            return {}

        cache_keys = [f"dp:promo_counts:{gid}" for gid in gym_ids]
        result_map = {}
        uncached_ids = []

        try:
            cached_values = await self.redis.mget(cache_keys)
            for gid, val in zip(gym_ids, cached_values):
                if val is not None:
                    raw = val.decode() if isinstance(val, bytes) else val
                    result_map[gid] = int(raw)
                else:
                    uncached_ids.append(gid)
        except (RedisError, ValueError):
            uncached_ids = list(gym_ids)

        if not uncached_ids:
            return result_map

        stmt = (
            select(DailyPass.gym_id, func.count(func.distinct(DailyPass.client_id)))
            .join(DailyPassDay, DailyPassDay.pass_id == DailyPass.id)
            .where(
                DailyPass.gym_id.in_([str(gid) for gid in uncached_ids]),
                DailyPass.status != "canceled",
                DailyPassDay.dailypass_price == OFFER_PRICE_RUPEES,
            )
            .group_by(DailyPass.gym_id)
        )
        result = await self.db.execute(stmt)
        db_counts = {int(row[0]): int(row[1]) for row in result.all()}

        try:
            pipe = self.redis.pipeline()
            for gid in uncached_ids:
                count = db_counts.get(gid, 0)
                result_map[gid] = count
                pipe.setex(f"dp:promo_counts:{gid}", CACHE_TTL_10MIN, str(count))
            await pipe.execute()
        except RedisError:
            for gid in uncached_ids:
                result_map[gid] = db_counts.get(gid, 0)

        return result_map
