"""Database & cache queries specific to Gym Membership.

Only membership-specific data access lives here.
Shared queries (gyms, views, frequently_booked) are in shared/.
Includes hydration logic for membership-enabled gym sets.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Dict, List, Optional, Set

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog
from app.config.pricing import get_markup_multiplier
from app.models.fittbot_models import GymPlans, NoCostEmi
from ..shared.utils import fetch_active_membership_offers
from ..shared.utils import round_per_month_price

MEMBERSHIP_ENABLED_SET_KEY = "set:membership:enabled"
MEMBERSHIP_REFRESH_KEY = "membership:last_refresh"
MEMBERSHIP_TTL_SECONDS = 3 * 60 * 60  # 3 hours

CACHE_TTL_10MIN = 10 * 60


class MembershipRepository:
    """Membership-specific data access only."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # -- Hydration -------------------------------------------------------------

    async def hydrate(self) -> bool:
        """Populate Redis membership-enabled set from DB. Lock-guarded."""
        lock_key = f"{MEMBERSHIP_REFRESH_KEY}:lock"
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)

        if not acquired:
            exists = await self.redis.exists(MEMBERSHIP_REFRESH_KEY)
            if exists:
                return False
            await asyncio.sleep(0.1)
            return not await self.redis.exists(MEMBERSHIP_REFRESH_KEY)

        try:
            exists = await self.redis.exists(MEMBERSHIP_REFRESH_KEY)
            if exists:
                await self.redis.delete(lock_key)
                return False

            stmt = (
                select(GymPlans.gym_id)
                .distinct()
            )
            result = await self.db.execute(stmt)
            gym_ids = [str(row[0]) for row in result.all()]

            pipe = self.redis.pipeline()
            pipe.delete(MEMBERSHIP_ENABLED_SET_KEY)

            if gym_ids:
                pipe.sadd(MEMBERSHIP_ENABLED_SET_KEY, *gym_ids)

            pipe.setex(MEMBERSHIP_REFRESH_KEY, MEMBERSHIP_TTL_SECONDS, str(len(gym_ids)))
            pipe.delete(lock_key)
            await pipe.execute()
            return True

        except RedisError as e:
            jlog("error", {
                "type": "cache_hydration_failure",
                "error_code": "MEMBERSHIP_HYDRATE_REDIS",
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
                "error_code": "MEMBERSHIP_HYDRATE_ERROR",
                "detail": str(e),
                "exc": repr(e),
            })
            try:
                await self.redis.delete(lock_key)
            except RedisError:
                pass
            return False

    # -- Queries ---------------------------------------------------------------

    async def get_membership_enabled_gym_ids(self) -> Set[int]:
        """Gym IDs that have at least one membership plan. Redis first, DB fallback."""
        try:
            members = await self.redis.smembers(MEMBERSHIP_ENABLED_SET_KEY)
            if members:
                return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "MEM_ENABLED_CACHE_MISS",
                "detail": str(e),
                "fallback": "database",
            })

        stmt = select(GymPlans.gym_id).distinct()
        result = await self.db.execute(stmt)
        return {int(r[0]) for r in result.all()}

    async def fetch_plans_for_gyms(self, gym_ids: List[int]) -> Dict[int, List]:
        """Fetch all GymPlans rows grouped by gym_id. Cached 10 min per gym."""
        if not gym_ids:
            return {}

        cache_keys = [f"membership:plans:{gid}" for gid in gym_ids]
        plans_map: Dict[int, List] = {}
        uncached_ids = []

        try:
            cached_values = await self.redis.mget(cache_keys)
            for gid, val in zip(gym_ids, cached_values):
                if val is not None:
                    raw = val.decode() if isinstance(val, bytes) else val
                    rows = json.loads(raw)
                    plans_map[gid] = [SimpleNamespace(**r) for r in rows]
                else:
                    uncached_ids.append(gid)
        except (RedisError, json.JSONDecodeError):
            uncached_ids = list(gym_ids)

        if not uncached_ids:
            return plans_map

        stmt = select(GymPlans).where(GymPlans.gym_id.in_(uncached_ids))
        result = await self.db.execute(stmt)

        per_gym: Dict[int, List] = {}
        for plan in result.scalars().all():
            per_gym.setdefault(plan.gym_id, []).append(plan)

        for gid in uncached_ids:
            plans_map[gid] = per_gym.get(gid, [])

        try:
            pipe = self.redis.pipeline()
            for gid in uncached_ids:
                serialized = [
                    {
                        "id": p.id, "gym_id": p.gym_id, "plans": p.plans,
                        "duration": p.duration, "amount": p.amount,
                        "personal_training": p.personal_training,
                        "plan_for": p.plan_for, "original_amount": p.original_amount,
                        "bonus": p.bonus, "bonus_type": p.bonus_type,
                        "pause": p.pause, "pause_type": p.pause_type,
                        "buddy_count": p.buddy_count, "sessions_count": p.sessions_count,
                        "description": p.description, "services": p.services,
                    }
                    for p in per_gym.get(gid, [])
                ]
                pipe.setex(f"membership:plans:{gid}", CACHE_TTL_10MIN, json.dumps(serialized))
            await pipe.execute()
        except RedisError:
            pass

        return plans_map

    async def fetch_emi_flags(self, gym_ids: List[int]) -> Dict[int, bool]:
        """Return {gym_id: True} for gyms with no-cost EMI enabled. Cached 10 min per gym."""
        if not gym_ids:
            return {}

        cache_keys = [f"membership:emi:{gid}" for gid in gym_ids]
        result_map: Dict[int, bool] = {}
        uncached_ids = []

        try:
            cached_values = await self.redis.mget(cache_keys)
            for gid, val in zip(gym_ids, cached_values):
                if val is not None:
                    raw = val.decode() if isinstance(val, bytes) else val
                    if raw == "1":
                        result_map[gid] = True
                else:
                    uncached_ids.append(gid)
        except RedisError:
            uncached_ids = list(gym_ids)

        if not uncached_ids:
            return result_map

        stmt = select(NoCostEmi).where(
            NoCostEmi.gym_id.in_(uncached_ids),
            NoCostEmi.no_cost_emi.is_(True),
        )
        result = await self.db.execute(stmt)
        found_ids = set()
        for row in result.scalars().all():
            result_map[row.gym_id] = True
            found_ids.add(row.gym_id)

        try:
            pipe = self.redis.pipeline()
            for gid in uncached_ids:
                pipe.setex(f"membership:emi:{gid}", CACHE_TTL_10MIN, "1" if gid in found_ids else "0")
            await pipe.execute()
        except RedisError:
            pass

        return result_map

    async def filter_by_no_cost_emi(self, candidate_ids: Set[int]) -> Set[int]:
        """Filter to gyms with no_cost_emi enabled AND at least one plan >= 4000."""
        if not candidate_ids:
            return set()

        emi_stmt = select(NoCostEmi.gym_id).where(
            NoCostEmi.gym_id.in_(candidate_ids),
            NoCostEmi.no_cost_emi.is_(True),
        )
        emi_result = await self.db.execute(emi_stmt)
        emi_enabled_ids = {row[0] for row in emi_result.all()}

        if not emi_enabled_ids:
            return set()

        plans_stmt = select(GymPlans.gym_id).where(
            GymPlans.gym_id.in_(emi_enabled_ids),
            GymPlans.amount >= 4000,
        ).distinct()
        plans_result = await self.db.execute(plans_stmt)
        return {row[0] for row in plans_result.all()}

    async def filter_by_membership_types(
        self, candidate_ids: Set[int], membership_types: List[str],
    ) -> Set[int]:
        """Filter to gyms with plans matching requested categories.

        Valid values: membership, pt, couple_membership, couple_pt, buddy, buddy_pt
        """
        if not candidate_ids or not membership_types:
            return candidate_ids

        mt_conditions = []
        for mt in membership_types:
            
            
            if mt == "membership":
                mt_conditions.append(
                    (GymPlans.personal_training.is_(False))
                    & (or_(GymPlans.plan_for.is_(None), GymPlans.plan_for.notin_(["couple", "buddy"])))
                )
            
            elif mt == "pt":
                mt_conditions.append(
                    (GymPlans.personal_training.is_(True))
                    & (or_(GymPlans.plan_for.is_(None), GymPlans.plan_for.notin_(["couple", "buddy"])))
                )
            
            elif mt == "couple_membership":
                mt_conditions.append(
                    (GymPlans.personal_training.is_(False)) & (GymPlans.plan_for == "couple")
                )
            elif mt == "couple_pt":
                mt_conditions.append(
                    (GymPlans.personal_training.is_(True)) & (GymPlans.plan_for == "couple")
                )
            elif mt == "buddy":
                mt_conditions.append(
                    (GymPlans.personal_training.is_(False)) & (GymPlans.plan_for == "buddy")
                )
            elif mt == "buddy_pt":
                mt_conditions.append(
                    (GymPlans.personal_training.is_(True)) & (GymPlans.plan_for == "buddy")
                )

        if not mt_conditions:
            return candidate_ids

        stmt = select(GymPlans.gym_id).where(
            GymPlans.gym_id.in_(candidate_ids),
            or_(*mt_conditions),
        ).distinct()
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}


   
    async def build_price_sort_map(
        self, gym_ids: List[int], membership_types: Optional[List[str]] = None,
    ) -> Dict[int, float]:

        if not gym_ids:
            return {}

        conditions = [GymPlans.gym_id.in_(gym_ids)]

        if membership_types:
            mt_conditions = []
            for mt in membership_types:
                if mt == "membership":
                    mt_conditions.append(
                        (GymPlans.personal_training.is_(False))
                        & (or_(GymPlans.plan_for.is_(None), GymPlans.plan_for.notin_(["couple", "buddy"])))
                    )
                elif mt == "pt":
                    mt_conditions.append(
                        (GymPlans.personal_training.is_(True))
                        & (or_(GymPlans.plan_for.is_(None), GymPlans.plan_for.notin_(["couple", "buddy"])))
                    )
                elif mt == "couple_membership":
                    mt_conditions.append(
                        (GymPlans.personal_training.is_(False)) & (GymPlans.plan_for == "couple")
                    )
                elif mt == "couple_pt":
                    mt_conditions.append(
                        (GymPlans.personal_training.is_(True)) & (GymPlans.plan_for == "couple")
                    )
                elif mt == "buddy":
                    mt_conditions.append(
                        (GymPlans.personal_training.is_(False)) & (GymPlans.plan_for == "buddy")
                    )
                elif mt == "buddy_pt":
                    mt_conditions.append(
                        (GymPlans.personal_training.is_(True)) & (GymPlans.plan_for == "buddy")
                    )
            
            
            if mt_conditions:
                conditions.append(or_(*mt_conditions))

        stmt = (
            select(
                GymPlans.gym_id,
                func.min(GymPlans.amount / GymPlans.duration).label("min_per_month"),
            )
            .where(*conditions)
            .group_by(GymPlans.gym_id)
        )

        result = await self.db.execute(stmt)

        price_map: Dict[int, float] = {}
        multiplier = get_markup_multiplier()
        for row in result.all():
            raw_per_month = float(row.min_per_month) * multiplier
            price_map[int(row.gym_id)] = round_per_month_price(raw_per_month)

        return price_map

    async def fetch_active_offers(self, gym_ids: List[int]) -> Dict[int, Dict]:
        """Fetch active membership offers for given gyms. Delegates to shared utility."""
        return await fetch_active_membership_offers(self.db, gym_ids)

