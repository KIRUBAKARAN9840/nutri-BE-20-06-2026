"""Database & cache queries specific to Sessions.

Only session-specific data access lives here.
Shared queries (gyms, views, frequently_booked) are in shared/.
Includes hydration logic (same pattern as DailyPassRepository).
"""

import asyncio
import json
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Dict, List, Optional, Set

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog

from app.models.fittbot_models import (
    Client,
    ClassSession,
    Gym,
    NewOffer,
    SessionBookingDay,
    SessionPurchase,
    SessionSchedule,
    SessionSetting,
)
from app.config.constants import GYM_OFFER_USER_CAP

HIDDEN_SESSION_IDS = {7, 8, 10, 11, 14}

SESSION_LOW_SET_KEY = "set:session:low99"
SESSION_ENABLED_SET_KEY = "set:session:enabled"
SESSION_REFRESH_KEY = "session:last_refresh"
SESSION_TTL_SECONDS = 3 * 60 * 60  # 3 hours

USER_SESSION_INELIGIBLE_KEY = "user:{client_id}:session_ineligible"
USER_SESSION_OFFER_ELIG_KEY = "user:{client_id}:session_offer_elig"
USER_SESSION_BOOKED_PROMO_KEY = "user:{client_id}:session_booked_promo"
SESSION_OFFER_PRICE = 99

# Cache TTLs
CACHE_TTL_10MIN = 10 * 60
CACHE_TTL_5MIN = 5 * 60
CACHE_TTL_USER_OFFER = 24 * 60 * 60  # 24h — invalidated by session_processor on paid booking


class SessionRepository:
    """Session-specific data access only."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # ── Hydration (populate Redis with session-enabled gym IDs) ──────

    async def hydrate(self) -> bool:
        """Populate Redis session enabled set + low99 set from DB. Lock-guarded."""
        lock_key = f"{SESSION_REFRESH_KEY}:lock"
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)

        if not acquired:
            exists = await self.redis.exists(SESSION_REFRESH_KEY)
            if exists:
                return False
            await asyncio.sleep(0.1)
            return not await self.redis.exists(SESSION_REFRESH_KEY)

        try:
            exists = await self.redis.exists(SESSION_REFRESH_KEY)
            if exists:
                await self.redis.delete(lock_key)
                return False

            # All gyms with enabled session settings (excluding hidden)
            enabled_stmt = (
                select(SessionSetting.gym_id)
                .join(Gym, Gym.gym_id == SessionSetting.gym_id)
                .where(
                    Gym.fittbot_verified.is_(True),
                    SessionSetting.is_enabled.is_(True),
                    SessionSetting.session_id.notin_(HIDDEN_SESSION_IDS),
                )
                .distinct()
            )
            enabled_result = await self.db.execute(enabled_stmt)
            enabled_rows = enabled_result.all()

            # Gyms with ₹99 session price
            low99_stmt = (
                select(SessionSetting.gym_id)
                .join(Gym, Gym.gym_id == SessionSetting.gym_id)
                .where(
                    Gym.fittbot_verified.is_(True),
                    SessionSetting.is_enabled.is_(True),
                    SessionSetting.final_price == SESSION_OFFER_PRICE,
                    SessionSetting.session_id.notin_(HIDDEN_SESSION_IDS),
                )
                .distinct()
            )
            low99_result = await self.db.execute(low99_stmt)
            low99_rows = low99_result.all()

            pipe = self.redis.pipeline()
            pipe.delete(SESSION_ENABLED_SET_KEY)
            pipe.delete(SESSION_LOW_SET_KEY)

            enabled_ids = [str(row.gym_id) for row in enabled_rows]
            low99_ids = [str(row.gym_id) for row in low99_rows]

            if enabled_ids:
                pipe.sadd(SESSION_ENABLED_SET_KEY, *enabled_ids)
            if low99_ids:
                pipe.sadd(SESSION_LOW_SET_KEY, *low99_ids)

            pipe.setex(SESSION_REFRESH_KEY, SESSION_TTL_SECONDS, str(len(enabled_rows)))
            pipe.delete(lock_key)
            await pipe.execute()
            return True

        except RedisError as e:
            jlog("error", {
                "type": "cache_hydration_failure",
                "error_code": "SESSION_HYDRATE_REDIS",
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
                "error_code": "SESSION_HYDRATE_ERROR",
                "detail": str(e),
                "exc": repr(e),
            })
            try:
                await self.redis.delete(lock_key)
            except RedisError:
                pass
            return False

    # ── User offer eligibility ───────────────────────────────────────

    async def get_user_offer_eligibility(self, client_id: Optional[int]) -> Dict:
        """Return client_name + (no-op) session offer eligibility.

        The ₹99 intro offer was removed: session pricing is now owner final_price
        plus commission for everyone. So we no longer query the booking count or
        read/write the session offer/ineligible Redis keys — eligibility is always
        False. client_name is still fetched because the response/UX uses it.
        """
        if not client_id:
            return {
                "session_count": 0,
                "session_offer_eligible": False,
                "client_name": None,
            }

        client_stmt = select(Client.name).where(Client.client_id == client_id)
        client_result = await self.db.execute(client_stmt)
        client_name = client_result.scalar()

        return {
            "session_count": 0,
            "session_offer_eligible": False,
            "client_name": client_name,
        }

    # ── Queries ──────────────────────────────────────────────────────

    async def get_session_name(self, session_id: int) -> Optional[str]:
        """Get session name by ID. Cached 10 min."""
        cache_key = f"session:name:{session_id}"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                return cached.decode() if isinstance(cached, bytes) else cached
        except RedisError:
            pass

        stmt = select(ClassSession.name).where(ClassSession.id == session_id)
        result = await self.db.execute(stmt)
        name = result.scalar()
        if name:
            try:
                await self.redis.setex(cache_key, CACHE_TTL_10MIN, name)
            except RedisError:
                pass
        return name

    async def get_session_enabled_gym_ids(self) -> Set[int]:
        """Gym IDs that have enabled session settings. Redis first, DB fallback."""
        try:
            members = await self.redis.smembers(SESSION_ENABLED_SET_KEY)
            if members:
                return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "SESSION_ENABLED_CACHE_MISS",
                "detail": str(e),
                "fallback": "database",
            })

        stmt = (
            select(SessionSetting.gym_id)
            .join(Gym, Gym.gym_id == SessionSetting.gym_id)
            .where(
                Gym.fittbot_verified.is_(True),
                SessionSetting.is_enabled.is_(True),
                SessionSetting.session_id.notin_(HIDDEN_SESSION_IDS),
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def get_gyms_with_session_on_date(
        self, session_id: int, target_date: date, candidate_ids: Set[int]
    ) -> Dict[int, List]:
        """
        Find gyms that have available (non-expired) slots for the given session on the given date.

        Returns {gym_id: [list of SessionSchedule rows with available slots]}.
        """
        if not candidate_ids:
            return {}

        weekday = target_date.weekday()  # 0=Monday .. 6=Sunday
        now = datetime.now()
        is_today = target_date == now.date()
        current_time = now.time()

        # Weekly schedules matching weekday + one-off schedules matching exact date
        stmt = (
            select(SessionSchedule)
            .where(
                SessionSchedule.gym_id.in_(candidate_ids),
                SessionSchedule.session_id == session_id,
                SessionSchedule.is_active.is_(True),
            )
        )
        result = await self.db.execute(stmt)
        all_schedules = result.scalars().all()

        gym_schedules: Dict[int, List] = {}
        for sched in all_schedules:
            # Match recurrence
            if sched.recurrence == "weekly" and sched.weekday != weekday:
                continue
            if sched.recurrence == "one_off" and sched.start_date != target_date:
                continue

            # Check date range bounds (if set)
            if sched.start_date and target_date < sched.start_date:
                continue
            if sched.end_date and target_date > sched.end_date:
                continue

            # Skip expired slots (if date is today, start_time must be in the future)
            if is_today and sched.start_time <= current_time:
                continue

            gym_schedules.setdefault(sched.gym_id, []).append(sched)

        return gym_schedules

    async def fetch_all_schedules(
        self, session_id: int, candidate_ids: Set[int]
    ) -> List:
        """Fetch all active schedules for session + candidates. Cached 10 min."""
        if not candidate_ids:
            return []

        cache_key = f"session:schedules:{session_id}"
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
                SessionSchedule.session_id == session_id,
                SessionSchedule.is_active.is_(True),
            )
        )
        result = await self.db.execute(stmt)
        all_rows = result.scalars().all()

        # Cache ALL schedules for this session (not filtered by candidate_ids)
        try:
            serialized = []
            for s in all_rows:
                serialized.append({
                    "id": s.id, "gym_id": s.gym_id, "session_id": s.session_id,
                    "trainer_id": s.trainer_id, "recurrence": s.recurrence,
                    "weekday": s.weekday, "slot_quota": s.slot_quota,
                    "start_date": s.start_date.isoformat() if s.start_date else None,
                    "end_date": s.end_date.isoformat() if s.end_date else None,
                    "start_time": s.start_time.isoformat() if s.start_time else None,
                    "end_time": s.end_time.isoformat() if s.end_time else None,
                })
            await self.redis.setex(cache_key, CACHE_TTL_10MIN, json.dumps(serialized))
        except RedisError:
            pass

        # Filter to candidate_ids for return
        return [s for s in all_rows if s.gym_id in candidate_ids]

    def filter_schedules_for_date(
        self, all_schedules: List, target_date: date
    ) -> Dict[int, List]:
        """Filter pre-fetched schedules for a specific date (pure Python, no DB)."""
        weekday = target_date.weekday()
        now = datetime.now()
        is_today = target_date == now.date()
        current_time = now.time()

        gym_schedules: Dict[int, List] = {}
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
            gym_schedules.setdefault(sched.gym_id, []).append(sched)
        return gym_schedules

    async def get_multi_date_booking_counts(
        self, schedule_ids: List[int], target_dates: List[date]
    ) -> Dict:
        """Fetch booking counts for multiple schedule_ids across multiple dates in ONE query.
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

    async def get_slot_availability(
        self, schedule_ids: List[int], target_date: date, gym_schedules_map: Dict[int, List]
    ) -> Dict[int, int]:
        """
        For each schedule_id, return the number of available slots.
        available = capacity - booked_count.

        Returns {schedule_id: available_slots}.
        """
        if not schedule_ids:
            return {}

        # Build capacity map from schedules
        capacity_map: Dict[int, int] = {}
        setting_ids_needed: Set[int] = set()
        for gym_id, schedules in gym_schedules_map.items():
            for sched in schedules:
                if sched.id in schedule_ids:
                    if sched.slot_quota is not None:
                        capacity_map[sched.id] = sched.slot_quota
                    else:
                        setting_ids_needed.add(sched.id)

        # Fetch default capacities from SessionSetting in ONE query (no N+1)
        if setting_ids_needed:
            needed_gym_ids = set()
            sched_to_keys = {}
            for gym_id, schedules in gym_schedules_map.items():
                for sched in schedules:
                    if sched.id in setting_ids_needed:
                        needed_gym_ids.add(sched.gym_id)
                        sched_to_keys[sched.id] = (sched.gym_id, sched.session_id, sched.trainer_id)

            cap_stmt = select(SessionSetting).where(
                SessionSetting.gym_id.in_(needed_gym_ids),
                SessionSetting.is_enabled.is_(True),
            )
            cap_result = await self.db.execute(cap_stmt)
            all_settings = cap_result.scalars().all()

            settings_by_key = {}
            for s in all_settings:
                settings_by_key[(s.gym_id, s.session_id, s.trainer_id)] = s
                settings_by_key.setdefault((s.gym_id, s.session_id), s)

            for sched_id, (gym_id, session_id, trainer_id) in sched_to_keys.items():
                setting = settings_by_key.get((gym_id, session_id, trainer_id))
                if setting is None and trainer_id is not None:
                    setting = settings_by_key.get((gym_id, session_id))
                capacity_map[sched_id] = setting.capacity if (setting and setting.capacity is not None) else 999

        # Count existing bookings per schedule on the target date
        booking_stmt = (
            select(
                SessionBookingDay.schedule_id,
                func.count(SessionBookingDay.id),
            )
            .where(
                SessionBookingDay.schedule_id.in_(schedule_ids),
                SessionBookingDay.booking_date == target_date,
                SessionBookingDay.status.in_(["booked", "attended", "no_show"]),
            )
            .group_by(SessionBookingDay.schedule_id)
        )
        booking_result = await self.db.execute(booking_stmt)
        booked_map = {row[0]: row[1] for row in booking_result.all()}

        availability: Dict[int, int] = {}
        for sched_id in schedule_ids:
            cap = capacity_map.get(sched_id, 999)
            booked = booked_map.get(sched_id, 0)
            available = max(0, cap - booked)
            availability[sched_id] = available

        return availability

    async def fetch_capacity_settings(self, gym_ids: Set[int]) -> Dict:
        """Fetch SessionSettings for capacity lookup. Cached 10 min.
        Returns {(gym_id, session_id, trainer_id): setting}."""
        if not gym_ids:
            return {}

        gym_list = sorted(gym_ids)
        cache_keys = [f"session:capacity:{gid}" for gid in gym_list]
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
            })

        try:
            pipe = self.redis.pipeline()
            for gid, rows in per_gym.items():
                pipe.setex(f"session:capacity:{gid}", CACHE_TTL_10MIN, json.dumps(rows))
            for gid in uncached_ids - set(per_gym.keys()):
                pipe.setex(f"session:capacity:{gid}", CACHE_TTL_10MIN, "[]")
            await pipe.execute()
        except RedisError:
            pass

        return settings_by_key

    async def get_session_low_gym_ids(self, candidate_ids: Set[int]) -> Set[int]:
        """Gym IDs eligible for ₹99 session offer: new_offer.session=true + promo count < 50 + user not already booked."""
        if not candidate_ids:
            return set()

        offer_map = await self.fetch_offer_flags(list(candidate_ids))
        promo_counts = await self.fetch_promo_counts(list(candidate_ids))

        return {
            gid for gid in candidate_ids
            if (offer_map.get(gid) and offer_map[gid].session
                and promo_counts.get(gid, 0) < GYM_OFFER_USER_CAP)
        }

    async def fetch_session_settings(self, gym_ids: List[int], session_id: int) -> Dict[int, SessionSetting]:
        """Fetch SessionSetting records for a specific session keyed by gym_id. Cached 10 min."""
        if not gym_ids:
            return {}

        cache_key = f"session:settings:{session_id}"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_settings = json.loads(raw)
                result_map = {}
                for gid in gym_ids:
                    gid_str = str(gid)
                    if gid_str in all_settings:
                        result_map[gid] = SimpleNamespace(**all_settings[gid_str])
                return result_map
        except (RedisError, json.JSONDecodeError):
            pass

        # Fetch ALL gyms for this session (not just requested gym_ids) so cache is complete
        stmt = select(SessionSetting).where(
            SessionSetting.session_id == session_id,
            SessionSetting.is_enabled.is_(True),
        )
        result = await self.db.execute(stmt)
        all_settings_map: Dict[int, SessionSetting] = {}
        for s in result.scalars().all():
            if s.gym_id not in all_settings_map or (s.final_price or 0) < (all_settings_map[s.gym_id].final_price or 0):
                all_settings_map[s.gym_id] = s

        # Cache ALL settings for this session_id
        try:
            serialized = {
                str(gid): {"gym_id": s.gym_id, "session_id": s.session_id, "final_price": s.final_price, "capacity": s.capacity}
                for gid, s in all_settings_map.items()
            }
            await self.redis.setex(cache_key, CACHE_TTL_10MIN, json.dumps(serialized))
        except RedisError:
            pass

        # Return only requested gym_ids
        return {gid: all_settings_map[gid] for gid in gym_ids if gid in all_settings_map}

    async def fetch_offer_flags(self, gym_ids: List[int]) -> Dict[int, NewOffer]:
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

        # Fetch ALL offers (not just requested gym_ids) so cache is complete
        stmt = select(NewOffer)
        result = await self.db.execute(stmt)
        all_offer_map = {row.gym_id: row for row in result.scalars().all()}

        try:
            serialized = {
                str(gid): {"gym_id": o.gym_id, "session": bool(o.session), "daily_pass": bool(o.daily_pass) if hasattr(o, 'daily_pass') else False}
                for gid, o in all_offer_map.items()
            }
            await self.redis.setex(cache_key, CACHE_TTL_10MIN, json.dumps(serialized))
        except (RedisError, AttributeError):
            pass

        # Return only requested gym_ids
        return {gid: all_offer_map[gid] for gid in gym_ids if gid in all_offer_map}

    async def fetch_promo_counts(self, gym_ids: List[int]) -> Dict[int, int]:
        """Count unique users who booked at ₹99 promo price per gym. Cached 5 min."""
        if not gym_ids:
            return {}

        cache_key = "session:promo_counts"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_counts = json.loads(raw)
                return {gid: all_counts.get(str(gid), 0) for gid in gym_ids}
        except (RedisError, json.JSONDecodeError):
            pass

        # Fetch ALL promo counts (not just requested gym_ids) so cache is complete
        distinct_clients_subquery = (
            select(SessionPurchase.gym_id, SessionPurchase.client_id)
            .select_from(SessionPurchase)
            .join(SessionBookingDay, SessionBookingDay.purchase_id == SessionPurchase.id)
            .where(
                SessionPurchase.status == "paid",
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

    async def fetch_user_booked_promo_gyms(self, client_id: Optional[int], gym_ids: List[int]) -> Set[int]:
        """Get gym IDs where user already booked a ₹99 session (can't get offer again at same gym).

        Caches the FULL set of promo-booked gym_ids per user for 24h (invalidated by
        session_processor on new paid booking). The lifetime cap of 3 promo sessions
        keeps the cached set tiny, so we filter against the requested gym_ids in memory.
        """
        if not client_id or not gym_ids:
            return set()

        cache_key = USER_SESSION_BOOKED_PROMO_KEY.format(client_id=client_id)
        requested = set(gym_ids)

        # Fast path: 24h cache hit — no DB at all
        try:
            cached = await self.redis.get(cache_key)
            if cached is not None:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_promo_gyms = {int(g) for g in json.loads(raw)}
                return all_promo_gyms & requested
        except (RedisError, json.JSONDecodeError, ValueError) as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "SESSION_BOOKED_PROMO_CACHE_READ",
                "detail": str(e),
                "client_id": client_id,
            })

        # DB miss: fetch ALL promo-booked gyms for this user (not just the requested ones)
        # so the cached set is reusable across home calls with different nearby gyms.
        stmt = (
            select(SessionPurchase.gym_id)
            .select_from(SessionPurchase)
            .join(SessionBookingDay, SessionBookingDay.purchase_id == SessionPurchase.id)
            .where(
                SessionPurchase.client_id == client_id,
                SessionPurchase.status == "paid",
                SessionBookingDay.status.in_(["booked", "attended", "no_show"]),
                SessionPurchase.price_per_session == SESSION_OFFER_PRICE,
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        all_promo_gyms = {int(row[0]) for row in result.all()}

        # Cache the full set (typically ≤3 entries) for 24h
        try:
            await self.redis.setex(
                cache_key, CACHE_TTL_USER_OFFER, json.dumps(list(all_promo_gyms)),
            )
        except RedisError as e:
            jlog("warning", {
                "type": "cache_write_failure",
                "error_code": "SESSION_BOOKED_PROMO_CACHE_WRITE",
                "detail": str(e),
                "client_id": client_id,
            })

        return all_promo_gyms & requested
