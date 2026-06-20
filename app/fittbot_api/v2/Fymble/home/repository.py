

import asyncio
import json
import uuid
from datetime import date, datetime, time
from typing import Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo


from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


from app.utils.logging_setup import jlog
from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.models.fittbot_models import ActiveUser, ClassSession, Client, GymPlans, GymStudiosPic, GymStudiosRequest, ReferralCode, SessionSchedule, Gym, SessionSetting
from app.models.nutrition_models import NutritionBooking, ClientDietTemplate, IphoneNutrition, WebinarRegistration
from app.fittbot_api.v1.payments.models.credits import CreditBalance, CreditLedger

NEW_USER_BONUS_CREDITS = 5
NEW_USER_BONUS_EXPIRY_DAYS = 7


from app.fittbot_api.v2.Fymble.fitness_studios.sessions.repository import HIDDEN_SESSION_IDS
from app.fittbot_api.v2.Fymble.fitness_studios.personal_training.repository import PERSONAL_TRAINING_SESSION_ID



PRIORITY_SESSION_NAMES = {"yoga", "pilates", "zumba"}

CACHE_TTL_RESPONSE = 5 * 60            # 5 minutes (legacy per-user response cache)
CACHE_TTL_PRIORITY_IDS = 24 * 60 * 60  # 24 hours
CACHE_TTL_SESSION_NAMES = 10 * 60      # 10 minutes
CACHE_TTL_GYM_INFO = 60 * 60           # 1 hour
CACHE_TTL_COVER_PICS = 60 * 60         # 1 hour
CACHE_TTL_GYM_PLANS = 5 * 60           # 5 minutes

# v2 split-cache TTLs
CACHE_TTL_LOCATION = 5 * 60            # 5 minutes (per-geohash, shared across users in cell)
CACHE_TTL_LOCATION_STALE = 30 * 60     # 30 minutes (stale fallback during stampede)
CACHE_TTL_USER_STATE = 60              # 60s (per-user time-sensitive state)
CACHE_TTL_REBUILD_LOCK = 30            # 30s (single-flight lock TTL)
CACHE_TTL_FIRST_TIME = 30 * 24 * 60 * 60  # 30 days (first_time flag survives until consumed)
CACHE_TTL_NONDP_LAST = 90 * 24 * 60 * 60  # 90 days (rotation pointer for rewards/refer)
CACHE_TTL_DAY = 24 * 60 * 60              # 24h day-scoped flags

REDIS_CALL_TIMEOUT = 0.05  # 50ms — skip cache if Redis is slower than this

_redis_breaker = CircuitBreaker(
    name="home-redis",
    failure_threshold=5,
    recovery_timeout=30.0,
    half_open_max_calls=3,
    success_threshold=2,
)

_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat: float, lng: float, precision: int = 6) -> str:
    """Encode lat/lng into a geohash string (~1.2km x 0.6km at precision 6)."""
    lat_range, lng_range = (-90.0, 90.0), (-180.0, 180.0)
    bits = (16, 8, 4, 2, 1)
    result = []
    ch = bit = 0
    is_lng = True
    while len(result) < precision:
        if is_lng:
            mid = (lng_range[0] + lng_range[1]) / 2
            if lng >= mid:
                ch |= bits[bit]
                lng_range = (mid, lng_range[1])
            else:
                lng_range = (lng_range[0], mid)
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                ch |= bits[bit]
                lat_range = (mid, lat_range[1])
            else:
                lat_range = (lat_range[0], mid)
        is_lng = not is_lng
        bit += 1
        if bit == 5:
            result.append(_GEOHASH_BASE32[ch])
            ch = bit = 0
    return "".join(result)


async def _safe_redis(coro, default=None):
    """Execute a Redis coroutine with 50ms timeout + circuit breaker.

    Returns *default* on timeout, Redis error, or open circuit.
    """
    try:
        _redis_breaker._before_call()
    except CircuitOpenError:
        return default
    try:
        result = await asyncio.wait_for(coro, timeout=REDIS_CALL_TIMEOUT)
        _redis_breaker._handle_success()
        return result
    except asyncio.TimeoutError:
        _redis_breaker._handle_failure(TimeoutError("Redis call exceeded 50ms"))
        return default
    except RedisError as e:
        _redis_breaker._handle_failure(e)
        return default


async def invalidate_user_state_cache(redis: Redis, client_id: int) -> None:

    await _safe_redis(redis.delete(f"home:v2:ustate:{client_id}"))


class HomeRepository:
    """Home feed data access + response caching."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # ── Geohash-based response cache ───────────────────────────

    async def get_cached_response(self, cache_key: str) -> Optional[dict]:
        """Return cached response. Geohash in the key handles location changes."""
        raw = await _safe_redis(self.redis.get(cache_key))
        if raw is None:
            return None
        try:
            data = raw.decode() if isinstance(raw, bytes) else raw
            return json.loads(data)
        except (json.JSONDecodeError, AttributeError) as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "HOME_RESPONSE_CACHE_READ",
                "detail": str(e),
                "cache_key": cache_key,
            })
            return None

    async def cache_response(
        self, cache_key: str, data: dict, ttl: int = CACHE_TTL_RESPONSE,
    ):
        """Cache the assembled response."""
        try:
            payload = json.dumps(data)
        except (TypeError, ValueError):
            return
        await _safe_redis(self.redis.setex(cache_key, ttl, payload))

    # ── v2 split-cache: pipelined initial read ──────────────────

    async def get_initial_state(
        self, client_id: int, geohash_str: str, today_iso: str,
    ) -> dict:
        """Single Redis pipeline read for everything we need at request entry.

        Returns:
            location: cached location payload (None if missing)
            location_stale: stale fallback payload (None if missing)
            user_state: cached user state (None if missing)
            first_time_consumed: True if first_time_user flag was just consumed (one-shot)
            dp_shown_existed: True if home:dp_shown:{cid}:{today} key already exists.
                              The actual SET NX is deferred to _resolve_promo_cards so
                              we only burn the once-per-day flag for ELIGIBLE users —
                              preserving original business logic exactly.
            nondp_shown_existed: True if home:nondp_shown:{cid}:{today} key already
                              exists (rewards/refer slot for today is already burned).
            nondp_last: Last non-DP promo type shown to this user ("rewards"/"refer"),
                        or None if never shown. Drives the rotation pointer.

        Atomically GETDELs the first_time flag (one-shot semantic).
        On Redis failure / open circuit, returns all-None / False (caller rebuilds fresh).
        """
        empty = {
            "location": None,
            "location_stale": None,
            "user_state": None,
            "first_time_consumed": False,
            "dp_shown_existed": False,
            "nondp_shown_existed": False,
            "nondp_last": None,
            "gymmate_last": None,
            "fc_dismissed": False,
        }
        if _redis_breaker.is_open:
            return empty

        loc_key = f"home:v2:loc:{geohash_str}"
        loc_stale_key = f"home:v2:loc:stale:{geohash_str}"
        ustate_key = f"home:v2:ustate:{client_id}"
        first_time_key = f"first_time_user:{client_id}"
        dp_shown_key = f"home:dp_shown:{client_id}:{today_iso}"
        nondp_shown_key = f"home:nondp_shown:{client_id}:{today_iso}"
        nondp_last_key = f"home:nondp_last:{client_id}"
        gymmate_last_key = f"home:gymmate_last:{client_id}"
        fc_dismissed_key = f"home:fc_dismissed:{client_id}"

        try:
            _redis_breaker._before_call()
        except CircuitOpenError:
            return empty

        try:
            pipe = self.redis.pipeline(transaction=False)
            pipe.get(loc_key)
            pipe.get(loc_stale_key)
            pipe.get(ustate_key)
            pipe.execute_command("GETDEL", first_time_key)
            pipe.get(dp_shown_key)         # READ-ONLY — SET NX deferred to eligible-user path
            pipe.get(nondp_shown_key)      # READ-ONLY — SET NX deferred to non-DP slot path
            pipe.get(nondp_last_key)       # slot-2 rotation pointer (9-item cycle / None)
            pipe.get(gymmate_last_key)     # slot-1 gymmate pointer ("gymmate1"/"gymmate2"/None)
            pipe.get(fc_dismissed_key)     # permanent free-credits-card dismissal flag
            raw = await asyncio.wait_for(pipe.execute(), timeout=REDIS_CALL_TIMEOUT * 4)
            _redis_breaker._handle_success()
        except (RedisError, asyncio.TimeoutError) as e:
            _redis_breaker._handle_failure(e)
            return empty

        def _decode_json(v):
            if v is None:
                return None
            try:
                return json.loads(v.decode() if isinstance(v, bytes) else v)
            except (json.JSONDecodeError, AttributeError) as e:
                jlog("warning", {
                    "type": "cache_decode_failure",
                    "error_code": "HOME_INITIAL_STATE_DECODE",
                    "detail": str(e),
                })
                return None

        def _decode_str(v):
            if v is None:
                return None
            return v.decode() if isinstance(v, bytes) else v

        return {
            "location": _decode_json(raw[0]),
            "location_stale": _decode_json(raw[1]),
            "user_state": _decode_json(raw[2]),
            "first_time_consumed": raw[3] is not None,
            "dp_shown_existed": raw[4] is not None,
            "nondp_shown_existed": raw[5] is not None,
            "nondp_last": _decode_str(raw[6]),
            "gymmate_last": _decode_str(raw[7]),
            "fc_dismissed": raw[8] is not None,
        }

    async def cache_location(self, geohash_str: str, data: dict):
        """Cache location-derived data fresh + stale (for stampede fallback)."""
        try:
            payload = json.dumps(data)
        except (TypeError, ValueError):
            return
        if _redis_breaker.is_open:
            return
        try:
            pipe = self.redis.pipeline(transaction=False)
            pipe.setex(f"home:v2:loc:{geohash_str}", CACHE_TTL_LOCATION, payload)
            pipe.setex(f"home:v2:loc:stale:{geohash_str}", CACHE_TTL_LOCATION_STALE, payload)
            await asyncio.wait_for(pipe.execute(), timeout=REDIS_CALL_TIMEOUT * 2)
            _redis_breaker._handle_success()
        except (RedisError, asyncio.TimeoutError) as e:
            _redis_breaker._handle_failure(e)

    async def try_acquire_rebuild_lock(self, geohash_str: str) -> bool:
        """Try SET NX for the location rebuild lock. Returns True if we got it.

        On Redis failure, returns True (degrade open: better to risk stampede
        than to never rebuild).
        """
        key = f"home:v2:loc:lock:{geohash_str}"
        result = await _safe_redis(
            self.redis.set(key, "1", nx=True, ex=CACHE_TTL_REBUILD_LOCK),
            default="OK",  # degrade open
        )
        return result is not None

    async def release_rebuild_lock(self, geohash_str: str):
        await _safe_redis(self.redis.delete(f"home:v2:loc:lock:{geohash_str}"))

    async def cache_user_state(self, client_id: int, data: dict):
        """Cache per-user time-sensitive state for 60s."""
        try:
            payload = json.dumps(data)
        except (TypeError, ValueError):
            return
        await _safe_redis(
            self.redis.setex(f"home:v2:ustate:{client_id}", CACHE_TTL_USER_STATE, payload)
        )

    # ── Free-credits card dismissal ──────────────────────────────

    async def dismiss_free_credits_card(self, client_id: int) -> None:
        """Permanently hide the free-credits welcome card for this client.

        Sets `home:fc_dismissed:{client_id}` with no TTL — the card only ever
        progresses active → expired (never back), so a permanent suppress is safe.
        Idempotent. Read back in get_initial_state's pipeline (no extra round trip).
        """
        await _safe_redis(
            self.redis.set(f"home:fc_dismissed:{client_id}", "1")
        )

    # ── Today's session schedules ────────────────────────────────

    async def fetch_today_schedules(
        self,
        gym_ids: Set[int],
        today: date,
        min_start_time: time,
    ) -> List:
        """Active schedules for today at given gyms — fully filtered in SQL."""
        if not gym_ids:
            return []

        weekday = today.weekday()

        stmt = (
            select(SessionSchedule)
            .where(
                SessionSchedule.gym_id.in_(gym_ids),
                SessionSchedule.is_active.is_(True),
                SessionSchedule.session_id.notin_(HIDDEN_SESSION_IDS),
                SessionSchedule.start_time >= min_start_time,
                # Date-range guard
                or_(SessionSchedule.start_date.is_(None), SessionSchedule.start_date <= today),
                or_(SessionSchedule.end_date.is_(None), SessionSchedule.end_date >= today),
                # Recurrence match
                or_(
                    and_(SessionSchedule.recurrence == "weekly", SessionSchedule.weekday == weekday),
                    and_(SessionSchedule.recurrence == "one_off", SessionSchedule.start_date == today),
                ),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    # ── Priority session IDs ─────────────────────────────────────

    async def resolve_priority_session_ids(self) -> Set[int]:
        """Resolve yoga/pilates/zumba to session IDs + PT (id=2). Cached 24hr."""
        cache_key = "home:priority_session_ids"
        cached = await _safe_redis(self.redis.get(cache_key))
        if cached:
            try:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                return {int(x) for x in json.loads(raw)} | {PERSONAL_TRAINING_SESSION_ID}
            except (json.JSONDecodeError, ValueError):
                pass

        stmt = select(ClassSession.id).where(
            func.lower(ClassSession.name).in_(PRIORITY_SESSION_NAMES)
        )
        result = await self.db.execute(stmt)
        ids = {row[0] for row in result.all()}

        await _safe_redis(
            self.redis.setex(cache_key, CACHE_TTL_PRIORITY_IDS, json.dumps(list(ids)))
        )
        return ids | {PERSONAL_TRAINING_SESSION_ID}

    # ── Bulk session names ───────────────────────────────────────

    async def fetch_session_names(self, session_ids: Set[int]) -> Dict[int, str]:
        """ClassSession.name for multiple IDs. Cached 10 min."""
        if not session_ids:
            return {}

        cache_key = "home:session_names"
        cached = await _safe_redis(self.redis.get(cache_key))
        if cached:
            try:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                all_names = json.loads(raw)
                return {sid: all_names[str(sid)] for sid in session_ids if str(sid) in all_names}
            except (json.JSONDecodeError, ValueError):
                pass

        stmt = select(ClassSession.id, ClassSession.name)
        result = await self.db.execute(stmt)
        all_names = {row[0]: row[1] for row in result.all()}

        serialized = {str(k): v for k, v in all_names.items()}
        await _safe_redis(
            self.redis.setex(cache_key, CACHE_TTL_SESSION_NAMES, json.dumps(serialized))
        )
        return {sid: all_names[sid] for sid in session_ids if sid in all_names}

    # ── Gym photos ───────────────────────────────────────────────

    async def fetch_all_photos(self, gym_ids: List[int]) -> Dict[int, List]:
        """All GymStudiosPic rows grouped by gym_id."""
        if not gym_ids:
            return {}

        stmt = select(GymStudiosPic).where(GymStudiosPic.gym_id.in_(gym_ids))
        result = await self.db.execute(stmt)

        photos_map: Dict[int, List] = {}
        for pic in result.scalars().all():
            photos_map.setdefault(pic.gym_id, []).append(pic)
        return photos_map

    # ── Cached gym info (1hr TTL) ────────────────────────────────

    async def fetch_gyms_cached(self, gym_ids: List[int]) -> Dict[int, dict]:
        """Fetch gym info with Redis cache. Returns {gym_id: {name, area, ...}}."""
        if not gym_ids:
            return {}

        result_map: Dict[int, dict] = {}
        miss_ids: List[int] = []

        # Redis pipeline read (with circuit breaker + timeout)
        if not _redis_breaker.is_open:
            try:
                pipe = self.redis.pipeline(transaction=False)
                for gid in gym_ids:
                    pipe.get(f"home:gym:{gid}")
                raw_values = await asyncio.wait_for(pipe.execute(), timeout=REDIS_CALL_TIMEOUT)
                _redis_breaker._handle_success()
                for gid, raw in zip(gym_ids, raw_values):
                    if raw is not None:
                        data = raw.decode() if isinstance(raw, bytes) else raw
                        result_map[gid] = json.loads(data)
                    else:
                        miss_ids.append(gid)
            except (RedisError, asyncio.TimeoutError) as e:
                _redis_breaker._handle_failure(e)
                miss_ids = list(gym_ids)
        else:
            miss_ids = list(gym_ids)

        # DB fallback
        if miss_ids:
            stmt = select(Gym).where(Gym.gym_id.in_(miss_ids))
            db_result = await self.db.execute(stmt)
            cache_pipe = self.redis.pipeline(transaction=False)
            for gym in db_result.scalars().all():
                gym_data = {
                    "gym_id": gym.gym_id,
                    "name": gym.name,
                    "area": gym.area,
                    "city": gym.city,
                    "cover_pic": gym.cover_pic,
                }
                result_map[gym.gym_id] = gym_data
                cache_pipe.setex(f"home:gym:{gym.gym_id}", CACHE_TTL_GYM_INFO, json.dumps(gym_data))
            await _safe_redis(cache_pipe.execute())

        return result_map

    # ── Cached cover pics (1hr TTL) ──────────────────────────────

    async def fetch_cover_pics_cached(self, gym_ids: List[int]) -> Dict[int, str]:
     
        if not gym_ids:
            return {}

        result_map: Dict[int, str] = {}
        miss_ids: List[int] = []

        if not _redis_breaker.is_open:
            try:
                pipe = self.redis.pipeline(transaction=False)
                for gid in gym_ids:
                    pipe.get(f"home:cover:{gid}")
                raw_values = await asyncio.wait_for(pipe.execute(), timeout=REDIS_CALL_TIMEOUT)
                _redis_breaker._handle_success()
                for gid, raw in zip(gym_ids, raw_values):
                    if raw is not None:
                        result_map[gid] = raw.decode() if isinstance(raw, bytes) else raw
                    else:
                        miss_ids.append(gid)
            except (RedisError, asyncio.TimeoutError) as e:
                _redis_breaker._handle_failure(e)
                miss_ids = list(gym_ids)
        else:
            miss_ids = list(gym_ids)

        if miss_ids:
            stmt = select(GymStudiosPic).where(
                GymStudiosPic.gym_id.in_(miss_ids),
                GymStudiosPic.type == "cover_pic",
            )
            db_result = await self.db.execute(stmt)
            cache_pipe = self.redis.pipeline(transaction=False)
            found_ids = set()
            for cp in db_result.scalars().all():
                result_map[cp.gym_id] = cp.image_url
                found_ids.add(cp.gym_id)
                cache_pipe.setex(f"home:cover:{cp.gym_id}", CACHE_TTL_COVER_PICS, cp.image_url)
            for gid in miss_ids:
                if gid not in found_ids:
                    result_map[gid] = ""
                    cache_pipe.setex(f"home:cover:{gid}", CACHE_TTL_COVER_PICS, "")
            await _safe_redis(cache_pipe.execute())

        return result_map

    # ── Cached gym plans (5min TTL) ──────────────────────────────

    async def fetch_plans_cached(self, gym_ids: List[int]) -> Dict[int, List]:
        """Fetch GymPlans with Redis cache. Returns {gym_id: [plan_dicts]}."""
        if not gym_ids:
            return {}

        result_map: Dict[int, List] = {}
        miss_ids: List[int] = []

        if not _redis_breaker.is_open:
            try:
                pipe = self.redis.pipeline(transaction=False)
                for gid in gym_ids:
                    pipe.get(f"home:plans:{gid}")
                raw_values = await asyncio.wait_for(pipe.execute(), timeout=REDIS_CALL_TIMEOUT)
                _redis_breaker._handle_success()
                for gid, raw in zip(gym_ids, raw_values):
                    if raw is not None:
                        data = raw.decode() if isinstance(raw, bytes) else raw
                        result_map[gid] = json.loads(data)
                    else:
                        miss_ids.append(gid)
            except (RedisError, asyncio.TimeoutError) as e:
                _redis_breaker._handle_failure(e)
                miss_ids = list(gym_ids)
        else:
            miss_ids = list(gym_ids)

        if miss_ids:
            stmt = select(GymPlans).where(GymPlans.gym_id.in_(miss_ids))
            db_result = await self.db.execute(stmt)
            plans_by_gym: Dict[int, List] = {}
            for plan in db_result.scalars().all():
                plan_data = {
                    "id": plan.id,
                    "gym_id": plan.gym_id,
                    "amount": float(plan.amount) if plan.amount else 0,
                    "duration": plan.duration,
                    "personal_training": plan.personal_training,
                    "plan_for": plan.plan_for,
                }
                plans_by_gym.setdefault(plan.gym_id, []).append(plan_data)

            cache_pipe = self.redis.pipeline(transaction=False)
            for gid in miss_ids:
                plans = plans_by_gym.get(gid, [])
                result_map[gid] = plans
                cache_pipe.setex(f"home:plans:{gid}", CACHE_TTL_GYM_PLANS, json.dumps(plans))
            await _safe_redis(cache_pipe.execute())

        return result_map

    # ── Credit balance (read-only, async) ────────────────────────

    async def track_daily_active_user(self, client_id: int, today: date) -> None:
        """Insert one active_users row per (client_id, day) on the first home call.

        Redis SET NX on `home:active_user:{cid}:{today}` (24h TTL) is the
        fast-path dedup gate — repeat calls within the day exit in O(1).
        DB is the source of truth: if Redis is flushed, the SET wins again
        but we still check active_users for an existing row before inserting,
        so no duplicates leak through.
        Best-effort: failures are logged, never raised, never block the response.
        """
        key = f"home:active_user:{client_id}:{today.isoformat()}"
        was_set = await _safe_redis(self.redis.set(key, "1", nx=True, ex=CACHE_TTL_DAY))
        if not was_set:
            return

        from app.models.async_database import get_async_sessionmaker
        AsyncSessionLocal = get_async_sessionmaker()
        try:
            async with AsyncSessionLocal() as session:
                exists_stmt = select(ActiveUser.id).where(
                    ActiveUser.client_id == client_id,
                    func.date(ActiveUser.created_at) == today,
                ).limit(1)
                existing = await session.execute(exists_stmt)
                if existing.scalar() is not None:
                    return
                session.add(ActiveUser(client_id=client_id))
                await session.commit()
        except Exception as e:
            jlog("warning", {
                "type": "active_user_insert_failure",
                "error_code": "HOME_ACTIVE_USER_INSERT",
                "client_id": client_id,
                "detail": str(e),
            })

    async def check_and_mark_dp_shown_today(self, client_id: int, today: date) -> bool:
        """Return True if this is the first home call today for this client, else False.

        Uses SET NX so only the first caller wins. Key expires at end of day.
        """
        key = f"home:dp_shown:{client_id}:{today.isoformat()}"
        was_set = await _safe_redis(self.redis.set(key, "1", nx=True, ex=CACHE_TTL_DAY))
        return bool(was_set)

    async def try_claim_nondp_slot(
        self, client_id: int, today: date, promo_type: str,
    ) -> bool:
        """Atomically claim today's non-DP modal slot for this client.

        SET NX home:nondp_shown:{cid}:{today} = promo_type, 24h TTL.
        Returns True if we won the race (modal should be shown), False otherwise.
        """
        key = f"home:nondp_shown:{client_id}:{today.isoformat()}"
        was_set = await _safe_redis(
            self.redis.set(key, promo_type, nx=True, ex=CACHE_TTL_DAY)
        )
        return bool(was_set)

    async def advance_nondp_pointer(self, client_id: int, promo_type: str) -> None:
        """Persist the rotation pointer so the next non-DP day picks the OTHER promo.

        90d TTL bounds Redis memory and gracefully resets the rotation for users
        who haven't seen a non-DP modal in 3 months.
        """
        key = f"home:nondp_last:{client_id}"
        await _safe_redis(
            self.redis.set(key, promo_type, ex=CACHE_TTL_NONDP_LAST)
        )

    async def advance_gymmate_pointer(self, client_id: int, gymmate_type: str) -> None:
        """Persist which gymmate modal (gymmate1/gymmate2) was last shown so
        the next day's slot-1 shows the OTHER one. 90d TTL — same bound as the
        non-DP pointer; a 3-month-idle user resets to gymmate1.
        """
        key = f"home:gymmate_last:{client_id}"
        await _safe_redis(
            self.redis.set(key, gymmate_type, ex=CACHE_TTL_NONDP_LAST)
        )

    async def fetch_client_profile(self, client_id: int) -> Optional[str]:
        """Return the client's profile pic URL, or None.

        Uses its own DB session to be safe inside asyncio.gather.
        """
        from app.models.async_database import get_async_sessionmaker

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = select(Client.profile).where(Client.client_id == client_id)
            result = await session.execute(stmt)
            return result.scalar()

    async def fetch_referral_code(self, client_id: int) -> Optional[str]:
        """Return the client's referral code with permanent Redis cache.

        Referral codes are immutable once issued at signup, so the cache key
        has no TTL — it's set once per user and reused forever. Uses its own
        DB session so it's safe inside asyncio.gather.
        """
        cache_key = f"referral:code:{client_id}"

        # Fast path: permanent Redis cache
        cached = await _safe_redis(self.redis.get(cache_key))
        if cached is not None:
            return cached.decode() if isinstance(cached, bytes) else cached

        # DB miss: query authoritative source
        from app.models.async_database import get_async_sessionmaker

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = select(ReferralCode.referral_code).where(
                ReferralCode.client_id == client_id,
            )
            result = await session.execute(stmt)
            code = result.scalar()

        if code:
            # Permanent cache — referral codes never change after issue
            await _safe_redis(self.redis.set(cache_key, code))

        return code

    async def fetch_credit_balance(self, client_id: int) -> int:
        """Fetch credit balance for home display. Sweeps expired grants first."""
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

    async def fetch_credit_balance_isolated(self, client_id: int) -> int:
        """Same as fetch_credit_balance but uses its own session — safe inside asyncio.gather.

        Grants a one-time new_user_bonus (3 credits) if the client has no
        credit_balances row yet. Row with balance=0 is left untouched.
        Sweeps expired signup/subscription grants before reading balance.
        """
        from app.models.async_database import get_async_sessionmaker
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service_async import (
            expire_stale_credits_in_session,
        )

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            expired = await expire_stale_credits_in_session(session, client_id)
            if expired > 0:
                await session.commit()
                # Invalidate home cache so next read sees fresh balance
                try:
                    keys = await self.redis.keys(f"home:data:{client_id}:*")
                    ustate_keys = await self.redis.keys(f"home:v2:ustate:{client_id}")
                    all_keys = (keys or []) + (ustate_keys or [])
                    if all_keys:
                        await self.redis.delete(*all_keys)
                except Exception:
                    pass

            stmt = select(CreditBalance).where(
                CreditBalance.client_id == client_id,
            )
            result = await session.execute(stmt)
            balance_row = result.scalars().first()

            if balance_row is not None:
                return balance_row.balance or 0

            dedup_key = f"signup_credits_{client_id}"
            ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
            from datetime import timedelta as _td
            expires_at = ist_now + _td(days=NEW_USER_BONUS_EXPIRY_DAYS)
            try:
                session.add(CreditBalance(
                    client_id=client_id,
                    balance=NEW_USER_BONUS_CREDITS,
                    total_purchased=0,
                    total_bonus=NEW_USER_BONUS_CREDITS,
                    total_used=0,
                ))
                session.add(CreditLedger(
                    id=f"crl_{int(ist_now.timestamp())}_{str(uuid.uuid4())[:8]}",
                    client_id=client_id,
                    txn_type="signup_bonus",
                    credits=NEW_USER_BONUS_CREDITS,
                    balance_after=NEW_USER_BONUS_CREDITS,
                    source_order_id=dedup_key,
                    description=f"Welcome bonus ({NEW_USER_BONUS_CREDITS} free credits, expires in {NEW_USER_BONUS_EXPIRY_DAYS}d)",
                    expires_at=expires_at,
                    created_at=ist_now,
                ))
                await session.commit()
                return NEW_USER_BONUS_CREDITS
            except IntegrityError:
                await session.rollback()
                bal_result = await session.execute(
                    select(CreditBalance.balance).where(CreditBalance.client_id == client_id)
                )
                return bal_result.scalar() or 0

    async def fetch_friend_suggestions(self, client_id: int) -> List[dict]:
        """Suggested gym-mates for the home page (same 3-tier waterfall the
        dedicated GymMate home uses: mutual → match → fallback, 1h rotation).

        Returns serialized dicts (not DTOs) so the list caches cleanly inside
        user_state. Own DB session — safe inside asyncio.gather; the rotation
        cache shares self.redis. Best-effort: returns [] on any error so the
        home page never breaks because of this section.
        """
        from app.models.async_database import get_async_sessionmaker

        AsyncSessionLocal = get_async_sessionmaker()
        try:
            async with AsyncSessionLocal() as session:
                from app.fittbot_api.v2.Fymble.gym_mate.friends import build_friends_api

                friends_api = build_friends_api(session, self.redis)
                suggestions = await friends_api.suggest_for_home(
                    client_id=client_id, limit=5,
                )
                return [s.model_dump() for s in suggestions]
        except Exception:
            return []

    async def fetch_last_dailypass_rebook(self, client_id: int) -> Optional[dict]:
        """Facts for the home 'rebook' card — the most-recently booked daily-pass
        gym, but ONLY when the client has no current/upcoming pass (we don't nudge
        a rebook while a booking is still live).

        Returns {gym_id:int, booking_count:int, last_booked_days_ago:int} or None.
        Two lightweight indexed queries on its own session — gather-safe.
        Best-effort: None on any error so the home page never breaks.
        """
        from app.models.async_database import get_async_sessionmaker
        from app.models.dailypass_models import DailyPass

        today = datetime.now().date()
        cid = str(client_id)  # daily_passes.client_id is a String column
        AsyncSessionLocal = get_async_sessionmaker()
        try:
            async with AsyncSessionLocal() as session:
                latest = (await session.execute(
                    select(
                        DailyPass.gym_id, DailyPass.created_at,
                        DailyPass.valid_until, DailyPass.status,
                    )
                    .where(DailyPass.client_id == cid)
                    .order_by(DailyPass.created_at.desc())
                    .limit(1)
                )).first()
                if latest is None:
                    return None

                gym_id_str, created_at, valid_until, status = latest

                # Suppress while a current/upcoming pass is live — no rebook
                # nudge yet. (Canceled passes don't count as live.)
                if (
                    valid_until is not None
                    and valid_until >= today
                    and status != "canceled"
                ):
                    return None

                try:
                    gym_id = int(gym_id_str)
                except (TypeError, ValueError):
                    return None

                count = (await session.execute(
                    select(func.count()).select_from(DailyPass).where(
                        DailyPass.client_id == cid,
                        DailyPass.gym_id == gym_id_str,
                    )
                )).scalar() or 0

                days_ago = (today - created_at.date()).days if created_at else None

                return {
                    "gym_id": gym_id,
                    "booking_count": int(count),
                    "last_booked_days_ago": days_ago,
                }
        except Exception:
            return None

    async def fetch_reward_total_entries(self, client_id: int) -> int:
        """Total valid reward-program entries for this client (0 if not opted in).

        Reuses RewardService.get_dashboard so the count stays identical to
        /api/v2/reward_program/dashboard. Own session — safe inside asyncio.gather.
        Best-effort: returns 0 on any error so the home page never breaks.
        """
        from app.models.async_database import get_async_sessionmaker

        AsyncSessionLocal = get_async_sessionmaker()
        try:
            async with AsyncSessionLocal() as session:
                from app.fittbot_api.v2.Fymble.rewards.service import RewardService
                data = await RewardService(session, self.redis).get_dashboard(client_id)
                return data.total_entries or 0
        except Exception:
            return 0

    async def fetch_unlimited_active(self, client_id: int) -> bool:
        """True if the client holds an active unlimited-scan pass (credit_999).

        Reads CreditBalance.unlimited_until and checks it is in the future.
        Uses its own session — safe inside asyncio.gather.
        """
        from app.models.async_database import get_async_sessionmaker

        ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = select(CreditBalance.client_id).where(
                CreditBalance.client_id == client_id,
                CreditBalance.unlimited_until.isnot(None),
                CreditBalance.unlimited_until > ist_now,
            )
            result = await session.execute(stmt)
            return result.scalar() is not None

    # ── Free credits card state ───────────────────────────────────

    async def fetch_free_credits_state(self, client_id: int) -> Optional[dict]:
        """Return {granted_at_iso, expires_at_iso, has_paid_plan} or None to hide card.

        - Reads the most recent signup_bonus ledger row.
        - expires_at drives days_left so manual DB edits are honored.
        - has_paid_plan = True if any 'purchase', 'subscription_bonus' or
          'scan_pass' (credit_999 unlimited) ledger row exists.
          When True, caller hides the card unconditionally.
        - Returns None when the user has no signup_bonus row.

        Uses its own DB session — safe inside asyncio.gather. Returns None on
        any exception so the home page never breaks because of this card.
        """
        from app.models.async_database import get_async_sessionmaker

        AsyncSessionLocal = get_async_sessionmaker()
        try:
            async with AsyncSessionLocal() as session:
                signup_stmt = (
                    select(CreditLedger.created_at, CreditLedger.expires_at)
                    .where(
                        CreditLedger.client_id == client_id,
                        CreditLedger.txn_type == "signup_bonus",
                    )
                    .order_by(CreditLedger.created_at.desc())
                    .limit(1)
                )
                row = (await session.execute(signup_stmt)).first()
                if row is None:
                    return None
                created_at, expires_at = row

                paid_stmt = (
                    select(CreditLedger.sno)
                    .where(
                        CreditLedger.client_id == client_id,
                        CreditLedger.txn_type.in_(("purchase", "subscription_bonus", "scan_pass")),
                    )
                    .limit(1)
                )
                has_paid_plan = (await session.execute(paid_stmt)).scalar() is not None

                return {
                    "granted_at_iso": created_at.isoformat() if created_at else None,
                    "expires_at_iso": expires_at.isoformat() if expires_at else None,
                    "has_paid_plan": has_paid_plan,
                }
        except Exception as e:
            jlog("warning", {
                "type": "free_credits_state_failure",
                "error_code": "HOME_FREE_CREDITS_STATE",
                "client_id": client_id,
                "detail": str(e),
            })
            return None

    # ── Nutrition status ──────────────────────────────────────────

    async def fetch_nutrition_status(self, client_id: int) -> Tuple[bool, bool, bool, Optional[dict], Optional[int]]:
        """Return (nutrition_purchased, diet_plan_assigned, not_attended, nutrition_schedule, booking_id).

        Updated for 4-session package flow:
          Priority 1: Upcoming 'booked' session → not_attended=True (show join card)
          Priority 2: Active package (remaining > 0) → most recent attended → check diet_plan_assigned
          Priority 2.5: Package depleted but last attended within 30 days → keep diet plan visible
          Priority 3: No relevant bookings → all False

        Active-package window is 180 days (matches nutri_1m validity, covers all 4 sessions).
        Post-completion window is 30 days from the most recent attended session — applies to
        both nutri_1m (4 sessions) and nutri_3m (12 sessions) once the package is exhausted.

        Uses its own DB session to avoid conflicts when called inside
        asyncio.gather alongside other queries on the request session.
        """
        from datetime import datetime, timedelta
        from app.models.async_database import get_async_sessionmaker

        today = datetime.now().date()
        cutoff = today - timedelta(days=180)
        post_completion_cutoff = today - timedelta(days=30)

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            from app.models.nutrition_models import NutritionEligibility

            # Check if client has an active package with remaining sessions
            active_pkg_stmt = (
                select(NutritionEligibility)
                .where(
                    NutritionEligibility.client_id == client_id,
                    NutritionEligibility.source_type == "fymble_purchase",
                    NutritionEligibility.remaining_sessions > 0,
                    or_(
                        NutritionEligibility.expires_at.is_(None),
                        NutritionEligibility.expires_at >= datetime.now(),
                    ),
                )
                .limit(1)
            )
            active_result = await session.execute(active_pkg_stmt)
            has_active_package = active_result.scalar_one_or_none() is not None

            # Priority 1: Upcoming 'booked' session → show join card
            upcoming_stmt = (
                select(NutritionBooking)
                .where(
                    NutritionBooking.client_id == client_id,
                    NutritionBooking.status == "booked",
                    NutritionBooking.booking_date >= today,
                )
                .order_by(NutritionBooking.booking_date.asc())
                .limit(1)
            )
            upcoming_result = await session.execute(upcoming_stmt)
            upcoming = upcoming_result.scalar_one_or_none()

            if upcoming:
                now = datetime.now().time()
                if upcoming.booking_date > today or upcoming.end_time >= now:
                    schedule = {
                        "booking_date": upcoming.booking_date.isoformat(),
                        "start_time": upcoming.start_time.strftime("%H:%M"),
                    }
                    return True, False, True, schedule, upcoming.id

            # Priority 2: Active package exists (purchased, sessions remaining)
            if has_active_package:
                # Check most recent attended booking for diet plan
                attended_stmt = (
                    select(NutritionBooking)
                    .where(
                        NutritionBooking.client_id == client_id,
                        NutritionBooking.status == "attended",
                        NutritionBooking.booking_date >= cutoff,
                    )
                    .order_by(NutritionBooking.booking_date.desc(), NutritionBooking.id.desc())
                    .limit(1)
                )
                attended_result = await session.execute(attended_stmt)
                attended = attended_result.scalar_one_or_none()

                if attended:
                    diet_stmt = select(func.count()).select_from(ClientDietTemplate).where(
                        ClientDietTemplate.client_id == client_id,
                        ClientDietTemplate.booking_id == attended.id,
                    )
                    diet_result = await session.execute(diet_stmt)
                    has_diet = diet_result.scalar() > 0
                    return True, has_diet, False, None, attended.id

                # Purchased but no bookings yet → nutrition_purchased = True
                return True, False, False, None, None

            # Priority 2.5: Package depleted — keep last diet plan visible for 30 days
            recent_attended_stmt = (
                select(NutritionBooking)
                .where(
                    NutritionBooking.client_id == client_id,
                    NutritionBooking.status == "attended",
                    NutritionBooking.booking_date >= post_completion_cutoff,
                )
                .order_by(NutritionBooking.booking_date.desc(), NutritionBooking.id.desc())
                .limit(1)
            )
            recent_attended = (await session.execute(recent_attended_stmt)).scalar_one_or_none()

            if recent_attended:
                had_purchase_stmt = (
                    select(NutritionEligibility.id)
                    .where(
                        NutritionEligibility.client_id == client_id,
                        NutritionEligibility.source_type == "fymble_purchase",
                    )
                    .limit(1)
                )
                had_purchase = (await session.execute(had_purchase_stmt)).scalar_one_or_none() is not None

                if had_purchase:
                    diet_stmt = select(func.count()).select_from(ClientDietTemplate).where(
                        ClientDietTemplate.client_id == client_id,
                        ClientDietTemplate.booking_id == recent_attended.id,
                    )
                    has_diet = (await session.execute(diet_stmt)).scalar() > 0
                    return True, has_diet, False, None, recent_attended.id

            # Priority 3: No active package and no recent diet plan to surface
            return False, False, False, None, None

    # ── AI diet coach booking ────────────────────────────────────

    async def fetch_active_ai_booking(self, client_id: int) -> bool:
        """True if client has an active, unexpired AI diet booking."""
        from app.models.async_database import get_async_sessionmaker
        from app.models.nutrition_models import AiDietBooking

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = (
                select(AiDietBooking.id)
                .where(
                    AiDietBooking.client_id == client_id,
                    AiDietBooking.status == "active",
                    AiDietBooking.expires_at > datetime.now(),
                )
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    # ── Apple PG plan-tier flags ─────────────────────────────────

    async def fetch_active_plan_flags(self, client_id: int) -> Dict[str, bool]:
        """Return per-tier active flags based on most recent captured payment.

        Window per tier (independent of plan validity):
          ai_diet_coach: True for 30 days after purchase
          expert (nutri_1m / nutrition_service_30): True for 30 days after purchase
          elite  (nutri_3m): True for 90 days after purchase

        Uses its own DB session — safe inside asyncio.gather.
        Date filtering happens in Postgres (avoids tz-aware/naive Python compare).
        """
        from datetime import timedelta
        from app.models.async_database import get_async_sessionmaker
        from app.fittbot_api.v1.payments.models.payments import Payment
        from app.fittbot_api.v1.payments.models.orders import OrderItem

        now = datetime.now()
        cutoff_30d = now - timedelta(days=30)
        cutoff_90d = now - timedelta(days=90)

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = (
                select(OrderItem.sku)
                .join(Payment, Payment.order_id == OrderItem.order_id)
                .where(
                    Payment.customer_id == str(client_id),
                    Payment.status == "captured",
                    or_(
                        and_(
                            OrderItem.sku == "ai_diet_coach",
                            Payment.captured_at >= cutoff_30d,
                        ),
                        and_(
                            OrderItem.sku == "nutri_3m",
                            Payment.captured_at >= cutoff_90d,
                        ),
                        and_(
                            OrderItem.sku.in_(("nutri_1m", "nutrition_service_30")),
                            Payment.captured_at >= cutoff_30d,
                        ),
                    ),
                )
                .distinct()
            )
            skus = {row[0] for row in (await session.execute(stmt)).all()}

        return {
            "ai_diet_coach": "ai_diet_coach" in skus,
            "elite": "nutri_3m" in skus,
            "expert": bool(skus & {"nutri_1m", "nutrition_service_30"}),
        }

    # ── Personal coach eligibility flag ──────────────────────────

    async def fetch_webinar_registered(self, client_id: int) -> bool:
        """True if client_id has a webinar_registrations row.

        Used by the home promo card rotation to suppress the webinar card
        once the user has registered. Own session — safe under asyncio.gather.
        """
        from app.models.async_database import get_async_sessionmaker

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = (
                select(WebinarRegistration.id)
                .where(WebinarRegistration.client_id == client_id)
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def fetch_personal_coach_active(self, client_id: int) -> bool:
        """True if client has an active (unused & unexpired) nutrition eligibility row."""
        from app.models.async_database import get_async_sessionmaker
        from app.models.nutrition_models import NutritionEligibility

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = (
                select(NutritionEligibility.id)
                .where(
                    NutritionEligibility.client_id == client_id,
                    NutritionEligibility.source_type == "fymble_purchase",
                    NutritionEligibility.remaining_sessions > 0,
                    or_(
                        NutritionEligibility.expires_at.is_(None),
                        NutritionEligibility.expires_at >= datetime.now(),
                    ),
                )
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    # ── Nutrition package status (4-session flow) ────────────────

    async def fetch_nutrition_package_status(self, client_id: int) -> Optional[dict]:
        """Return nutrition package card data for home page.

        Uses its own DB session to be safe inside asyncio.gather.
        Returns None if the client has no active package.
        """
        from datetime import datetime, timedelta, date as date_type
        from app.models.async_database import get_async_sessionmaker
        from app.models.nutrition_models import NutritionEligibility

        SESSION_SCHEDULE = [
            {"seq": 1, "duration_minutes": 60, "unlock_after_days": 0},
            {"seq": 2, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 3, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 4, "duration_minutes": 60, "unlock_after_days": 7},
        ]

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = (
                select(NutritionEligibility)
                .where(
                    NutritionEligibility.client_id == client_id,
                    NutritionEligibility.remaining_sessions > 0,
                    NutritionEligibility.source_type == "fymble_purchase",
                    or_(
                        NutritionEligibility.expires_at.is_(None),
                        NutritionEligibility.expires_at >= datetime.now(),
                    ),
                )
                .order_by(NutritionEligibility.granted_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            eligibility = result.scalar_one_or_none()

            if not eligibility:
                return None

            # Only show card for 4-session packages (has session_schedule)
            if not eligibility.session_schedule:
                return None

            next_seq = eligibility.used_sessions + 1
            schedule = eligibility.session_schedule or SESSION_SCHEDULE

            next_session = None
            for s in schedule:
                if s["seq"] == next_seq:
                    next_session = s
                    break

            unlocked = True
            unlock_date = None
            if next_session and next_session["unlock_after_days"] > 0 and eligibility.last_booking_date:
                unlock_date_val = eligibility.last_booking_date + timedelta(
                    days=next_session["unlock_after_days"]
                )
                unlocked = date_type.today() >= unlock_date_val
                if not unlocked:
                    unlock_date = unlock_date_val.isoformat()

            return {
                "has_active_package": True,
                "total_sessions": eligibility.total_sessions,
                "sessions_used": eligibility.used_sessions,
                "sessions_remaining": eligibility.remaining_sessions,
                "next_session_number": next_seq if next_session else None,
                "next_session_duration": next_session["duration_minutes"] if next_session else None,
                "next_session_unlocked": unlocked,
                "next_unlock_date": unlock_date,
                "eligibility_id": eligibility.id,
            }

    # ── Bulk session settings (single DB query for all session_ids) ──

    async def fetch_bulk_session_settings(
        self, gym_ids: List[int], session_ids: List[int],
    ) -> Dict[Tuple[int, int], dict]:
        """Fetch SessionSettings for multiple session_ids in one query.

        Returns {(gym_id, session_id): {final_price, capacity, ...}}.
        Uses per-session_id Redis cache (same keys as SessionRepository)
        so other callers benefit too.
        """
        if not gym_ids or not session_ids:
            return {}

        result: Dict[Tuple[int, int], dict] = {}
        miss_sids: List[int] = []

        # 1. Check Redis cache for each session_id (pipeline — single round trip)
        if not _redis_breaker.is_open:
            try:
                pipe = self.redis.pipeline(transaction=False)
                for sid in session_ids:
                    pipe.get(f"session:settings:{sid}")
                raw_values = await asyncio.wait_for(pipe.execute(), timeout=REDIS_CALL_TIMEOUT)
                _redis_breaker._handle_success()

                for sid, raw in zip(session_ids, raw_values):
                    if raw is not None:
                        data = raw.decode() if isinstance(raw, bytes) else raw
                        all_settings = json.loads(data)
                        for gid in gym_ids:
                            gid_str = str(gid)
                            if gid_str in all_settings:
                                result[(gid, sid)] = all_settings[gid_str]
                    else:
                        miss_sids.append(sid)
            except (RedisError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                if isinstance(e, (RedisError, asyncio.TimeoutError)):
                    _redis_breaker._handle_failure(e)
                miss_sids = list(session_ids)
        else:
            miss_sids = list(session_ids)

        if not miss_sids:
            return result

        # 2. Single DB query for ALL missing session_ids
        stmt = select(SessionSetting).where(
            SessionSetting.session_id.in_(miss_sids),
            SessionSetting.is_enabled.is_(True),
        )
        db_result = await self.db.execute(stmt)

        # Group by session_id, keep lowest final_price per (gym_id, session_id)
        by_session: Dict[int, Dict[int, dict]] = {}
        for s in db_result.scalars().all():
            session_map = by_session.setdefault(s.session_id, {})
            existing = session_map.get(s.gym_id)
            if existing is None or (s.final_price or 0) < (existing.get("final_price") or 0):
                session_map[s.gym_id] = {
                    "gym_id": s.gym_id,
                    "session_id": s.session_id,
                    "final_price": s.final_price,
                    "capacity": s.capacity,
                }

        # 3. Populate result + cache back to Redis
        cache_pipe = self.redis.pipeline(transaction=False)
        for sid in miss_sids:
            session_map = by_session.get(sid, {})
            serialized = {str(gid): data for gid, data in session_map.items()}
            cache_pipe.setex(f"session:settings:{sid}", 10 * 60, json.dumps(serialized))
            for gid in gym_ids:
                if gid in session_map:
                    result[(gid, sid)] = session_map[gid]
        await _safe_redis(cache_pipe.execute())

        return result

    # ── Nutrition slots available (next working day) ─────────────

    async def fetch_nutrition_slots_available(self) -> int:

        from datetime import datetime, timedelta
        from app.models.async_database import get_async_sessionmaker
        from app.models.nutrition_models import NutritionSchedule, NutritionBooking

        target_date = (datetime.now() + timedelta(days=1)).date()
        # Skip Sunday (weekday 6) — nutritionist is on leave
        if target_date.weekday() == 6:
            target_date = target_date + timedelta(days=1)
        weekday = target_date.weekday()

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            # Total active schedule slots for the target weekday
            result = await session.execute(
                select(NutritionSchedule.id).where(
                    NutritionSchedule.nutritionist_id == 1,
                    NutritionSchedule.is_active.is_(True),
                    NutritionSchedule.weekday == weekday,
                    or_(NutritionSchedule.start_date.is_(None), NutritionSchedule.start_date <= target_date),
                    or_(NutritionSchedule.end_date.is_(None), NutritionSchedule.end_date >= target_date),
                )
            )
            total_slots = len(result.all())

            if total_slots == 0:
                return 0

            # Booked slots for the target date
            booked_result = await session.execute(
                select(func.count()).select_from(NutritionBooking).where(
                    NutritionBooking.nutritionist_id == 1,
                    NutritionBooking.booking_date == target_date,
                    NutritionBooking.status.in_(["booked", "pending", "attended"]),
                )
            )
            booked_count = booked_result.scalar() or 0

            return max(total_slots - booked_count, 0)

    # ── Active bookings check ─────────────────────────────────

    async def check_active_bookings(self, client_id: int) -> dict:
        """Return {dailypass, sessions, gym_membership} booleans.

        Uses its own DB session to be safe inside asyncio.gather.
        """
        from datetime import datetime
        from app.models.async_database import get_async_sessionmaker
        from app.models.dailypass_models import DailyPass
        from app.models.fittbot_models import SessionBookingDay, SessionPurchase
        from app.models.fittbot_models.gym import FittbotGymMembership

        today = datetime.now().date()
        AsyncSessionLocal = get_async_sessionmaker()

        async with AsyncSessionLocal() as session:
            dp = await session.execute(
                select(DailyPass.id)
                .where(DailyPass.client_id == client_id, DailyPass.valid_until >= today)
                .limit(1)
            )
            sess = await session.execute(
                select(SessionBookingDay.id)
                .join(SessionPurchase, SessionPurchase.id == SessionBookingDay.purchase_id)
                .where(
                    SessionBookingDay.client_id == client_id,
                    SessionBookingDay.booking_date >= today,
                    SessionBookingDay.status == "booked",
                    SessionPurchase.status == "paid",
                )
                .limit(1)
            )
            mem = await session.execute(
                select(FittbotGymMembership.id)
                .where(
                    FittbotGymMembership.client_id == str(client_id),
                    FittbotGymMembership.status == "upcoming",
                )
                .limit(1)
            )

            return {
                "dailypass": dp.scalar() is not None,
                "sessions": sess.scalar() is not None,
                "gym_membership": mem.scalar() is not None,
            }

    # ── Gym request (save_request) ──────────────────────────────

    async def get_active_nutrition_booking(
        self, booking_id: int, client_id: int,
    ):
        """Fetch a nutrition booking by ID + client with active status."""
        stmt = (
            select(NutritionBooking)
            .where(
                NutritionBooking.id == booking_id,
                NutritionBooking.client_id == client_id,
                NutritionBooking.status.in_(["booked", "pending"]),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def save_gym_request(
        self,
        client_id: int,
        lat: Optional[float],
        lng: Optional[float],
        area: Optional[str],
        city: Optional[str],
        state: Optional[str],
        pincode: Optional[str],
    ) -> bool:
        """Save a gym request. One request per client ever.

        Returns False if client already has any row, True on first save.
        """
        # Redis check first — avoids DB hit on repeat calls
        dedup_key = f"home:gym_request:{client_id}"
        cached = await _safe_redis(self.redis.get(dedup_key))
        if cached:
            return False

        # DB check — authoritative
        stmt = select(func.count()).select_from(GymStudiosRequest).where(
            GymStudiosRequest.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        if result.scalar() > 0:
            await _safe_redis(self.redis.set(dedup_key, "1"))
            return False

        record = GymStudiosRequest(
            client_id=client_id,
            lat=lat,
            lng=lng,
            area=area,
            city=city,
            state=state,
            pincode=pincode,
        )
        self.db.add(record)
        await self.db.commit()
        await _safe_redis(self.redis.set(dedup_key, "1"))
        return True

    async def upsert_iphone_nutrition(self, client_id: int, type_: str) -> bool:
        """Insert (client_id, type) into iphone_nutrition if absent.

        Returns True if a new row was added, False if a row already existed.
        """
        stmt = select(IphoneNutrition.id).where(
            IphoneNutrition.client_id == client_id,
            IphoneNutrition.type == type_,
        ).limit(1)
        result = await self.db.execute(stmt)
        if result.scalar() is not None:
            return False

        try:
            self.db.add(IphoneNutrition(client_id=client_id, type=type_))
            await self.db.commit()
            return True
        except IntegrityError:
            await self.db.rollback()
            return False
