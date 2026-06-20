"""Database & cache queries specific to Personal Training listing.

Only PT-specific data access lives here.
Reuses SessionSchedule, SessionSetting, SessionBookingDay models (session_id=2).
Trainer data comes from TrainerProfile.
"""

import asyncio
import json
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Dict, List, Optional, Set, Tuple

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.constants import GYM_OFFER_USER_CAP
from app.utils.logging_setup import jlog

from app.models.fittbot_models import (
    Client,
    Gym,
    NewOffer,
    SessionBookingDay,
    SessionPurchase,
    SessionSchedule,
    SessionSetting,
    TrainerProfile,
)

PERSONAL_TRAINING_SESSION_ID = 2
SESSION_OFFER_PRICE = 99

PT_ENABLED_SET_KEY = "set:pt:enabled"
PT_REFRESH_KEY = "pt:last_refresh"
PT_TTL_SECONDS = 3 * 60 * 60  # 3 hours

USER_PT_INELIGIBLE_KEY = "user:{client_id}:pt_ineligible"

CACHE_TTL_10MIN = 10 * 60
CACHE_TTL_5MIN = 5 * 60


class PTRepository:
    """Personal training-specific data access."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # ── Hydration ────────────────────────────────────────────────────

    async def hydrate(self) -> bool:
        """Populate Redis with PT-enabled gym IDs (session_id=2). Lock-guarded."""
        lock_key = f"{PT_REFRESH_KEY}:lock"
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)

        if not acquired:
            exists = await self.redis.exists(PT_REFRESH_KEY)
            if exists:
                return False
            await asyncio.sleep(0.1)
            return not await self.redis.exists(PT_REFRESH_KEY)

        try:
            exists = await self.redis.exists(PT_REFRESH_KEY)
            if exists:
                await self.redis.delete(lock_key)
                return False

            stmt = (
                select(SessionSetting.gym_id)
                .join(Gym, Gym.gym_id == SessionSetting.gym_id)
                .where(
                    Gym.fittbot_verified.is_(True),
                    SessionSetting.is_enabled.is_(True),
                    SessionSetting.session_id == PERSONAL_TRAINING_SESSION_ID,
                )
                .distinct()
            )
            result = await self.db.execute(stmt)
            rows = result.all()

            pipe = self.redis.pipeline()
            pipe.delete(PT_ENABLED_SET_KEY)

            enabled_ids = [str(row.gym_id) for row in rows]
            if enabled_ids:
                pipe.sadd(PT_ENABLED_SET_KEY, *enabled_ids)

            pipe.setex(PT_REFRESH_KEY, PT_TTL_SECONDS, str(len(rows)))
            pipe.delete(lock_key)
            await pipe.execute()
            return True

        except RedisError as e:
            jlog("error", {
                "type": "cache_hydration_failure",
                "error_code": "PT_HYDRATE_REDIS",
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
                "error_code": "PT_HYDRATE_ERROR",
                "detail": str(e),
                "exc": repr(e),
            })
            try:
                await self.redis.delete(lock_key)
            except RedisError:
                pass
            return False

    # ── PT-enabled gym IDs ───────────────────────────────────────────

    async def get_pt_enabled_gym_ids(self) -> Set[int]:
        """Gym IDs with enabled PT session settings. Redis first, DB fallback."""
        try:
            members = await self.redis.smembers(PT_ENABLED_SET_KEY)
            if members:
                return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "PT_ENABLED_CACHE_MISS",
                "detail": str(e),
                "fallback": "database",
            })

        stmt = (
            select(SessionSetting.gym_id)
            .join(Gym, Gym.gym_id == SessionSetting.gym_id)
            .where(
                Gym.fittbot_verified.is_(True),
                SessionSetting.is_enabled.is_(True),
                SessionSetting.session_id == PERSONAL_TRAINING_SESSION_ID,
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    # ── Trainer queries ──────────────────────────────────────────────

    async def fetch_trainers_for_gyms(
        self, gym_ids: Set[int],
    ) -> Dict[int, List]:
        """Fetch TrainerProfile rows per gym, ordered by created_at ASC.

        Returns {gym_id: [TrainerProfile, ...]} — earliest-created first.
        Only includes trainers that have an enabled PT SessionSetting.
        Cached 10 min (complete cache for all PT trainers).
        """
        if not gym_ids:
            return {}

        cache_key = "pt:trainers"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_trainers = json.loads(raw)
                result_map: Dict[int, List] = {}
                for gid in gym_ids:
                    gid_str = str(gid)
                    if gid_str in all_trainers:
                        result_map[gid] = [
                            SimpleNamespace(**t) for t in all_trainers[gid_str]
                        ]
                return result_map
        except (RedisError, json.JSONDecodeError):
            pass

        # Trainers with enabled PT settings at these gyms
        stmt = (
            select(TrainerProfile)
            .join(
                SessionSetting,
                (SessionSetting.gym_id == TrainerProfile.gym_id)
                & (SessionSetting.trainer_id == TrainerProfile.trainer_id),
            )
            .where(
                SessionSetting.session_id == PERSONAL_TRAINING_SESSION_ID,
                SessionSetting.is_enabled.is_(True),
            )
            .order_by(TrainerProfile.created_at.asc())
        )
        result = await self.db.execute(stmt)
        trainers = result.scalars().all()

        # Cache ALL PT trainers (not just requested gym_ids)
        all_by_gym: Dict[int, List] = {}
        for tp in trainers:
            all_by_gym.setdefault(tp.gym_id, []).append(tp)

        try:
            serialized: Dict[str, List] = {}
            for gid, tps in all_by_gym.items():
                serialized[str(gid)] = [
                    {
                        "trainer_id": tp.trainer_id,
                        "gym_id": tp.gym_id,
                        "full_name": tp.full_name,
                        "profile_image": tp.profile_image,
                        "experience": tp.experience,
                        "created_at": tp.created_at.isoformat() if tp.created_at else None,
                    }
                    for tp in tps
                ]
            await self.redis.setex(cache_key, CACHE_TTL_10MIN, json.dumps(serialized))
        except RedisError:
            pass

        return {gid: all_by_gym[gid] for gid in gym_ids if gid in all_by_gym}

    # ── Schedule queries ─────────────────────────────────────────────

    async def fetch_all_pt_schedules(
        self, candidate_ids: Set[int],
    ) -> List:
        """Fetch all active PT schedules (session_id=2) for candidates. Cached 10 min."""
        if not candidate_ids:
            return []

        cache_key = f"pt:schedules:{PERSONAL_TRAINING_SESSION_ID}"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                rows = json.loads(raw)
                all_scheds = []
                for r in rows:
                    if r["gym_id"] not in candidate_ids:
                        continue
                    ns = SimpleNamespace(**r)
                    ns.start_date = date.fromisoformat(r["start_date"]) if r.get("start_date") else None
                    ns.end_date = date.fromisoformat(r["end_date"]) if r.get("end_date") else None
                    ns.start_time = time.fromisoformat(r["start_time"]) if r.get("start_time") else None
                    ns.end_time = time.fromisoformat(r["end_time"]) if r.get("end_time") else None
                    all_scheds.append(ns)
                return all_scheds
        except (RedisError, json.JSONDecodeError, Exception):
            pass

        stmt = (
            select(SessionSchedule)
            .where(
                SessionSchedule.session_id == PERSONAL_TRAINING_SESSION_ID,
                SessionSchedule.is_active.is_(True),
            )
        )
        result = await self.db.execute(stmt)
        all_rows = result.scalars().all()

        # Cache ALL PT schedules (not filtered by candidate_ids)
        try:
            serialized = []
            for s in all_rows:
                serialized.append({
                    "id": s.id, "gym_id": s.gym_id,
                    "session_id": s.session_id, "trainer_id": s.trainer_id,
                    "recurrence": s.recurrence, "weekday": s.weekday,
                    "slot_quota": s.slot_quota,
                    "start_date": s.start_date.isoformat() if s.start_date else None,
                    "end_date": s.end_date.isoformat() if s.end_date else None,
                    "start_time": s.start_time.isoformat() if s.start_time else None,
                    "end_time": s.end_time.isoformat() if s.end_time else None,
                })
            await self.redis.setex(cache_key, CACHE_TTL_10MIN, json.dumps(serialized))
        except RedisError:
            pass

        return [s for s in all_rows if s.gym_id in candidate_ids]

    def filter_schedules_for_date(
        self, all_schedules: List, target_date: date,
    ) -> Dict[int, Dict[int, List]]:
        """Filter pre-fetched PT schedules for a specific date.

        Returns {gym_id: {trainer_id: [schedule, ...]}} — grouped by gym then trainer.
        """
        weekday = target_date.weekday()
        now = datetime.now()
        is_today = target_date == now.date()
        current_time = now.time()

        gym_trainer_schedules: Dict[int, Dict[int, List]] = {}
        for sched in all_schedules:
            if sched.recurrence == "weekly" and sched.weekday != weekday:
                continue
            if sched.recurrence == "one_off" and sched.start_date != target_date:
                continue
            if sched.start_date and target_date < sched.start_date:
                continue
            if sched.end_date and target_date > sched.end_date:
                continue
            if is_today and sched.start_time <= current_time:
                continue
            trainer_id = sched.trainer_id or 0
            (
                gym_trainer_schedules
                .setdefault(sched.gym_id, {})
                .setdefault(trainer_id, [])
                .append(sched)
            )
        return gym_trainer_schedules

    # ── Booking counts ───────────────────────────────────────────────

    async def get_multi_date_booking_counts(
        self, schedule_ids: List[int], target_dates: List[date],
    ) -> Dict:
        """Fetch booking counts for schedule_ids across dates in ONE query.
        Returns {(schedule_id, booking_date): count}."""
        if not schedule_ids or not target_dates:
            return {}
        stmt = (
            select(
                SessionBookingDay.schedule_id,
                SessionBookingDay.booking_date,
                func.count(SessionBookingDay.id),
            )
            .where(
                SessionBookingDay.schedule_id.in_(schedule_ids),
                SessionBookingDay.booking_date.in_(target_dates),
                SessionBookingDay.status.in_(["booked", "attended", "no_show"]),
            )
            .group_by(SessionBookingDay.schedule_id, SessionBookingDay.booking_date)
        )
        result = await self.db.execute(stmt)
        return {(row[0], row[1]): row[2] for row in result.all()}

    # ── Capacity ─────────────────────────────────────────────────────

    async def fetch_capacity_settings(self, gym_ids: Set[int]) -> Dict:
        """Fetch SessionSettings for capacity lookup. Cached 10 min.
        Returns {(gym_id, session_id, trainer_id): setting}."""
        if not gym_ids:
            return {}

        gym_list = sorted(gym_ids)
        cache_keys = [f"pt:capacity:{gid}" for gid in gym_list]
        settings_by_key = {}
        uncached_ids = set()

        try:
            cached_values = await self.redis.mget(cache_keys)
            for gid, val in zip(gym_list, cached_values):
                if val is not None:
                    raw = val.decode() if isinstance(val, bytes) else val
                    rows = json.loads(raw)
                    for r in rows:
                        ns = SimpleNamespace(**r)
                        settings_by_key[(r["gym_id"], r["session_id"], r.get("trainer_id"))] = ns
                        settings_by_key.setdefault((r["gym_id"], r["session_id"]), ns)
                else:
                    uncached_ids.add(gid)
        except (RedisError, json.JSONDecodeError):
            uncached_ids = gym_ids

        if not uncached_ids:
            return settings_by_key

        stmt = select(SessionSetting).where(
            SessionSetting.gym_id.in_(uncached_ids),
            SessionSetting.session_id == PERSONAL_TRAINING_SESSION_ID,
            SessionSetting.is_enabled.is_(True),
        )
        result = await self.db.execute(stmt)

        per_gym: Dict[int, List] = {}
        for s in result.scalars().all():
            settings_by_key[(s.gym_id, s.session_id, s.trainer_id)] = s
            settings_by_key.setdefault((s.gym_id, s.session_id), s)
            per_gym.setdefault(s.gym_id, []).append({
                "gym_id": s.gym_id, "session_id": s.session_id,
                "trainer_id": s.trainer_id, "capacity": s.capacity,
                "final_price": s.final_price,
            })

        try:
            pipe = self.redis.pipeline()
            for gid, rows in per_gym.items():
                pipe.setex(f"pt:capacity:{gid}", CACHE_TTL_10MIN, json.dumps(rows))
            for gid in uncached_ids - set(per_gym.keys()):
                pipe.setex(f"pt:capacity:{gid}", CACHE_TTL_10MIN, "[]")
            await pipe.execute()
        except RedisError:
            pass

        return settings_by_key

    # ── User offer eligibility ───────────────────────────────────────

    async def get_user_offer_eligibility(self, client_id: Optional[int]) -> Dict:
        """Check if user is eligible for PT session offer. Returns client_name, count, eligibility."""
        if not client_id:
            return {"session_count": 0, "session_offer_eligible": False, "client_name": None}

        ineligible_key = USER_PT_INELIGIBLE_KEY.format(client_id=client_id)

        ineligible_cached = False
        try:
            ineligible_cached = await self.redis.exists(ineligible_key)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "PT_INELIGIBLE_CACHE_READ",
                "detail": str(e),
                "client_id": client_id,
            })

        client_stmt = select(Client.name).where(Client.client_id == client_id)
        client_result = await self.db.execute(client_stmt)
        client_name = client_result.scalar()

        if ineligible_cached:
            session_count = 3
        else:
            session_stmt = (
                select(func.count())
                .select_from(SessionBookingDay)
                .join(SessionPurchase, SessionPurchase.id == SessionBookingDay.purchase_id)
                .where(
                    SessionBookingDay.client_id == client_id,
                    SessionBookingDay.status.in_(["booked", "attended", "no_show"]),
                    SessionPurchase.status == "paid",
                )
            )
            session_result = await self.db.execute(session_stmt)
            session_count = session_result.scalar() or 0

            if session_count >= 3:
                try:
                    await self.redis.set(ineligible_key, "1")
                except RedisError as e:
                    jlog("warning", {
                        "type": "cache_write_failure",
                        "error_code": "PT_INELIGIBLE_CACHE_WRITE",
                        "detail": str(e),
                        "client_id": client_id,
                    })

        return {
            "session_count": session_count,
            "session_offer_eligible": session_count < 3,
            "client_name": client_name,
        }

    # ── Session settings (for pricing) ───────────────────────────────

    async def fetch_pt_settings(self, gym_ids: List[int]) -> Dict[Tuple[int, Optional[int]], object]:
        """Fetch PT SessionSetting records keyed by (gym_id, trainer_id). Cached 10 min."""
        if not gym_ids:
            return {}

        cache_key = f"pt:settings:{PERSONAL_TRAINING_SESSION_ID}"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_settings = json.loads(raw)
                result_map = {}
                for gid in gym_ids:
                    gid_str = str(gid)
                    if gid_str in all_settings:
                        for entry in all_settings[gid_str]:
                            ns = SimpleNamespace(**entry)
                            result_map[(gid, entry.get("trainer_id"))] = ns
                return result_map
        except (RedisError, json.JSONDecodeError):
            pass

        stmt = select(SessionSetting).where(
            SessionSetting.session_id == PERSONAL_TRAINING_SESSION_ID,
            SessionSetting.is_enabled.is_(True),
        )
        result = await self.db.execute(stmt)
        all_map: Dict[Tuple[int, Optional[int]], object] = {}
        per_gym_cache: Dict[str, List] = {}
        for s in result.scalars().all():
            all_map[(s.gym_id, s.trainer_id)] = s
            per_gym_cache.setdefault(str(s.gym_id), []).append({
                "gym_id": s.gym_id, "trainer_id": s.trainer_id,
                "session_id": s.session_id, "final_price": s.final_price,
                "capacity": s.capacity,
            })

        try:
            await self.redis.setex(cache_key, CACHE_TTL_10MIN, json.dumps(per_gym_cache))
        except RedisError:
            pass

        return {k: v for k, v in all_map.items() if k[0] in gym_ids}

    # ── Offer flags + promo counts (reuse session offer logic) ───────

    async def fetch_offer_flags(self, gym_ids: List[int]) -> Dict[int, object]:
        """Fetch NewOffer rows keyed by gym_id. Cached 10 min."""
        if not gym_ids:
            return {}

        cache_key = "session:offer_flags"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_offers = json.loads(raw)
                result_map = {}
                for gid in gym_ids:
                    gid_str = str(gid)
                    if gid_str in all_offers:
                        result_map[gid] = SimpleNamespace(**all_offers[gid_str])
                return result_map
        except (RedisError, json.JSONDecodeError):
            pass

        stmt = select(NewOffer)
        result = await self.db.execute(stmt)
        all_offer_map = {row.gym_id: row for row in result.scalars().all()}

        try:
            serialized = {
                str(gid): {
                    "gym_id": o.gym_id,
                    "session": bool(o.session),
                    "daily_pass": bool(o.daily_pass) if hasattr(o, "daily_pass") else False,
                }
                for gid, o in all_offer_map.items()
            }
            await self.redis.setex(cache_key, CACHE_TTL_10MIN, json.dumps(serialized))
        except (RedisError, AttributeError):
            pass

        return {gid: all_offer_map[gid] for gid in gym_ids if gid in all_offer_map}

    async def fetch_promo_counts(self, gym_ids: List[int]) -> Dict[int, int]:
        """Count unique users who booked PT at ₹99 promo price per gym. Cached 5 min."""
        if not gym_ids:
            return {}

        cache_key = "pt:promo_counts"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_counts = json.loads(raw)
                return {gid: all_counts.get(str(gid), 0) for gid in gym_ids}
        except (RedisError, json.JSONDecodeError):
            pass

        distinct_clients_subquery = (
            select(SessionPurchase.gym_id, SessionPurchase.client_id)
            .select_from(SessionPurchase)
            .join(SessionBookingDay, SessionBookingDay.purchase_id == SessionPurchase.id)
            .where(
                SessionPurchase.status == "paid",
                SessionPurchase.session_id == PERSONAL_TRAINING_SESSION_ID,
                SessionBookingDay.status.in_(["booked", "attended", "no_show"]),
                SessionPurchase.price_per_session == SESSION_OFFER_PRICE,
            )
            .distinct()
        ).subquery()

        stmt = (
            select(
                distinct_clients_subquery.c.gym_id,
                func.count(distinct_clients_subquery.c.client_id),
            )
            .group_by(distinct_clients_subquery.c.gym_id)
        )
        result = await self.db.execute(stmt)
        counts = {int(row[0]): int(row[1]) for row in result.all()}

        try:
            await self.redis.setex(cache_key, CACHE_TTL_5MIN, json.dumps({str(k): v for k, v in counts.items()}))
        except RedisError:
            pass

        return counts

    async def fetch_user_booked_promo_gyms(
        self, client_id: Optional[int], gym_ids: List[int],
    ) -> Set[int]:
        """Gym IDs where user already booked a PT promo session."""
        if not client_id or not gym_ids:
            return set()

        stmt = (
            select(SessionPurchase.gym_id)
            .select_from(SessionPurchase)
            .join(SessionBookingDay, SessionBookingDay.purchase_id == SessionPurchase.id)
            .where(
                SessionPurchase.client_id == client_id,
                SessionPurchase.gym_id.in_(gym_ids),
                SessionPurchase.session_id == PERSONAL_TRAINING_SESSION_ID,
                SessionPurchase.status == "paid",
                SessionBookingDay.status.in_(["booked", "attended", "no_show"]),
                SessionPurchase.price_per_session == SESSION_OFFER_PRICE,
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        return {int(row[0]) for row in result.all()}

    async def get_pt_low_gym_ids(self, candidate_ids: Set[int]) -> Set[int]:
        """Gym IDs eligible for PT offer: new_offer.session=true + promo count < 50."""
        if not candidate_ids:
            return set()

        offer_map = await self.fetch_offer_flags(list(candidate_ids))
        promo_counts = await self.fetch_promo_counts(list(candidate_ids))

        return {
            gid for gid in candidate_ids
            if (offer_map.get(gid) and offer_map[gid].session
                and promo_counts.get(gid, 0) < GYM_OFFER_USER_CAP)
        }
