

import asyncio
import heapq
import logging
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional, Set, Tuple


WEBINAR_PROMO_CUTOFF = date(2026, 5, 29)



from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.pricing import (
    get_daily_offer_discount, get_markup_multiplier,
    get_walkaway_redis_key, apply_walkaway_discount,
    compute_session_price_rupees,
)
from app.fittbot_api.v2.Fymble.fitness_studios.shared.utils import (
    fetch_active_membership_offers,
    resolve_offer_base_amount,
)
from app.models.async_database import get_async_sessionmaker
from app.fittbot_api.v2.Fymble.fitness_studios.shared.base_listing_service import BaseListingService
from app.fittbot_api.v2.Fymble.fitness_studios.shared.utils import to_12hr, smart_round_price
from app.fittbot_api.v2.Fymble.fitness_studios.sessions.repository import SessionRepository
from app.fittbot_api.v2.Fymble.fitness_studios.gym_membership.repository import MembershipRepository
from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.repository import DailyPassRepository
from .repository import HomeRepository, geohash
from app.utils.logging_utils import FittbotHTTPException
from .schemas import (
    ActiveBookings,
    FreeCreditsCard,
    FrequentlyBookedGym,
    HomeDataParams,
    HomeDataResponse,
    HomeFestivalOffer,
    HomeMembershipGym,
    HomeSessionSlot,
    NutritionJoinData,
    NutritionJoinResponse,
)

FREE_CREDITS_WINDOW_DAYS = 7

NEARBY_RADIUS_KM = 10.0
MEMBERSHIP_LIMIT = 5
SLOT_LEAD_MINUTES = 30

_HOME_GIFS = ["99_offer", "falling_gif", "ai_diet", "ai_diet_coach", "199_plan", "gym_mate"]

# Until this date (inclusive) the home GIF is locked to "gym_mate" as a launch
# promo. After it, "gym_mate" simply takes its turn in the normal daily rotation.
GYM_MATE_PROMO_CUTOFF = date(2026, 6, 20)


_SESSION_NAME_PRIORITY = {"personal training": 0, "yoga": 1, "zumba": 2, "pilates": 3}


_PLAN_TYPE_PRIORITY = {
    "individual": 0, None: 0, "": 0,
    "personal": 1, "couple": 2, "buddy": 3,
}


def _today_home_gif() -> str:
    
    if datetime.now().date() <= GYM_MATE_PROMO_CUTOFF:
        return "gym_mate"
    return _HOME_GIFS[datetime.now().timetuple().tm_yday % len(_HOME_GIFS)]


def _time_to_minutes(raw_time: str) -> int:

    h, m = raw_time.split(":")
    return int(h) * 60 + int(m)


def _select_display_plan(plans: List[dict], gym_offers: dict = None) -> Optional[Tuple[int, int, int]]:

    if not plans:
        return None

    def _sort_key(p):
        pt = p.get("personal_training", False)
        plan_for = p.get("plan_for")
        if pt:
            prio = {"couple": 2, "buddy": 3}.get(plan_for, 1)
        else:
            prio = _PLAN_TYPE_PRIORITY.get(plan_for, 0)

        return (-p.get("duration", 0), prio, p.get("amount", 0))

    best = min(plans, key=_sort_key)
    multiplier = get_markup_multiplier()

   
    base, _ = resolve_offer_base_amount(best["id"], best["amount"], gym_offers or {})
    price = smart_round_price(base * multiplier)

    daily_discount = get_daily_offer_discount()
    if daily_discount > 0:
        price = max(price - daily_discount, 0)
    return price, best["id"], best["duration"]


class HomeService(BaseListingService):

    _error_code_prefix = "HOME"

    def __init__(self, db: AsyncSession, redis: Redis):
        super().__init__(db, redis)
        self.home_repo = HomeRepository(db, redis)
        self.sess_repo = SessionRepository(db, redis)
        self.mem_repo = MembershipRepository(db, redis)
        self.dp_repo = DailyPassRepository(db, redis)

    async def get_home_data(self, params: HomeDataParams) -> HomeDataResponse:

        geo_hash = geohash(params.client_lat, params.client_lng)
        today = datetime.now().date()
        today_iso = today.isoformat()


        asyncio.create_task(
            self.home_repo.track_daily_active_user(params.client_id, today)
        )

        # ── Step 1: Single pipelined Redis read ──
        initial = await self.home_repo.get_initial_state(
            params.client_id, geo_hash, today_iso,
        )
        is_first_time = initial["first_time_consumed"]
        nondp_shown_existed = initial["nondp_shown_existed"]
        dp_shown_existed = initial["dp_shown_existed"]
        nondp_last = initial["nondp_last"]
        gymmate_last = initial["gymmate_last"]


        location, user_state = await asyncio.gather(
            self._resolve_location(params, geo_hash, initial),
            self._resolve_user_state(params.client_id, initial),
        )


        promo_task = self._resolve_promo_cards(
            params.client_id,
            nondp_shown_existed,
            dp_shown_existed,
            nondp_last,
            gymmate_last,
        )
        # Unified modal field — the single modal (if any) selected this call.
        # gymmate modals aren't in flags, so the resolver returns it directly.
        promo_flags, webinar_eligible, passes_left, referral_code, promo_modal = (
            await promo_task
        )

        # ── Step 4: Map cached session slots to response (intro/gym offer removed) ──
        session_slots_base = location.get("session_slots_base", [])
        nearby_sessions = (
            self._build_session_slots(session_slots_base)
            if session_slots_base else []
        )


        earliest_slot: Optional[str] = None
        if nearby_sessions:
            _t12 = nearby_sessions[0]["start_time"]  # e.g. "6:30 AM"
            _time_part, _period = _t12.rsplit(" ", 1)
            _h_str, _m_str = _time_part.split(":")
            _h = int(_h_str)
            if _period == "AM" and _h == 12:
                _h = 0
            elif _period == "PM" and _h != 12:
                _h += 12
            earliest_slot = f"{_h:02d}:{_m_str}"

        # ── Step 5: Assemble response ──
        bookings_dict = user_state.get("bookings") or {}
        bookings_obj = (
            ActiveBookings(**bookings_dict) if any(bookings_dict.values()) else None
        )

        # Apply walkaway 5% discount overlay to membership per_month prices
        walkaway_key = get_walkaway_redis_key(params.client_id)
        walkaway_active = bool(await self.redis.exists(walkaway_key))
        raw_memberships = location.get("nearby_memberships", [])
        if walkaway_active and raw_memberships:
            raw_memberships = [
                {**m, "per_month_price": apply_walkaway_discount(m["per_month_price"])}
                if m.get("per_month_price") else m
                for m in raw_memberships
            ]

        # ── Free-credits welcome card (5 scans / 7 days) ──
        # Suppressed permanently once the user dismisses it (cross on the zero card).
        if initial.get("fc_dismissed") or user_state.get("is_unlimited"):
            # Hide the welcome card once dismissed, or for unlimited-pass holders.
            free_credits_card = None
        else:
            _fc_state = user_state.get("free_credits")
            _balance = user_state.get("credits", 0)
            free_credits_card = self._build_free_credits_card(_fc_state, _balance, today)


        gym_mate_onboarding, gym_mate_nearby, gym_mate_friend_requests = (
            await self._fetch_gym_mate_blocks(params)
        )
        # Suggested friends are cached per-user in user_state (built on cache
        # miss, served free on hit) — not re-run on every call.
        gym_mate_friend_suggestions = user_state.get("friend_suggestions", [])

        # Rebook card — facts cached in user_state; enrich with gym info +
        # distance here. None when there's no past pass / a live pass exists.
        frequently_booked = await self._build_frequently_booked(
            params, user_state.get("frequently_booked"),
        )

        response = HomeDataResponse(
            profile=user_state.get("profile"),
            credits=user_state.get("credits", 0),
            is_unlimited=user_state.get("is_unlimited", False),
            total_entries=user_state.get("reward_total_entries", 0),
            free_credits_card=free_credits_card,
            modal=promo_modal,
            dailypass_eligibility=promo_flags["dailypass"],
            rewards_eligibility=promo_flags["rewards"],
            refer_eligibility=promo_flags["refer"],
            diet_eligibility=promo_flags["diet"],
            workout_eligibility=promo_flags["workout"],
            step_eligibility=promo_flags["step"],
            water_eligibility=promo_flags["water"],
            webinar_eligibility=webinar_eligible,
            referral_code=referral_code,
            no_of_passes_left=passes_left,
            ai=user_state.get("ai", True),
            personal_coach=user_state.get("personal_coach", True),
            bookings=bookings_obj,
            home_gif=_today_home_gif(),
            nearby_sessions=nearby_sessions,
            next_day=bool(location.get("session_next_day", False)),
            earliest_slot=earliest_slot,
            nearby_memberships=raw_memberships,
            festival_offers=location.get("festival_offers", []),
            dailypass_gyms=location.get("dailypass_gyms", []),
            first_time_user=is_first_time,
            gym_mate_onboarding=gym_mate_onboarding,
            gym_mate_nearby=gym_mate_nearby,
            gym_mate_friend_requests=gym_mate_friend_requests,
            gym_mate_friend_suggestions=gym_mate_friend_suggestions,
            frequently_booked=frequently_booked,
        )
        return response

    async def _fetch_gym_mate_blocks(self, params: HomeDataParams):

        try:
            from app.fittbot_api.v2.Fymble.gym_mate.friends import build_friends_api
            from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
                HomeFriendRequestsDotDTO, build_notifications_api,
            )
            from app.fittbot_api.v2.Fymble.gym_mate.profile import build_profile_api
            from app.fittbot_api.v2.Fymble.gym_mate.sessions import build_sessions_api

            profile_api = build_profile_api(self.db, self.redis)
            sessions_api = build_sessions_api(self.db, self.redis)
            friends_api = build_friends_api(self.db, self.redis)
            notifications_api = build_notifications_api(self.db)

            onboarding = await profile_api.get_status(params.client_id)
            distance_map = await self.geo.get_nearby_distances(
                params.client_lat, params.client_lng, NEARBY_RADIUS_KM,
            )
            nearby = await sessions_api.list_nearby_gym_mates(
                viewer_client_id=params.client_id,
                distance_map=distance_map,
                limit=5,
            ) if distance_map else []

            # Friend-requests block: count + has_unread from notifications
            # (same source the dedicated GymMate home uses), plus top-3
            # sender DPs from the friends repo for the avatar stack.
            notif_summary = await notifications_api.home_summary(
                recipient_client_id=params.client_id,
            )
            fr_block = notif_summary.friend_requests
            if fr_block.count > 0:
                avatars = await friends_api.recent_request_sender_avatars(
                    client_id=params.client_id, limit=3,
                )
                fr_block = HomeFriendRequestsDotDTO(
                    has_unread=fr_block.has_unread,
                    count=fr_block.count,
                    recent_avatars=avatars,
                )
            return onboarding, nearby, fr_block
        except Exception:
            logging.exception("gym_mate blocks failed for home/data")
            return None, [], None

    async def _build_frequently_booked(
        self, params: HomeDataParams, facts: Optional[dict],
    ) -> Optional[FrequentlyBookedGym]:
        """Enrich the cached rebook facts (gym_id + count + days-ago) with gym
        info + distance into the response card.

        Light: gym info is the cached `fetch_gym_info` (the same helper the
        dailypass/gym_mate cards use), and distance reuses the existing geo
        radius search — null when the gym is outside the nearby radius. Returns
        None when there's nothing to rebook. Best-effort: None on any failure.
        """
        if not facts:
            return None
        try:
            from app.fittbot_api.v2.Fymble.fitness_studios.shared.gym_price_enricher import (
                fetch_gym_info,
            )
            gym_id = facts["gym_id"]
            info_map = await fetch_gym_info(self.db, self.redis, [gym_id])
            gi = info_map.get(gym_id)
            if gi is None:
                return None

            distance_km = None
            try:
                dm = await self.geo.get_nearby_distances(
                    params.client_lat, params.client_lng, NEARBY_RADIUS_KM,
                )
                if gym_id in dm:
                    distance_km = round(dm[gym_id], 2)
            except Exception:
                distance_km = None

            return FrequentlyBookedGym(
                gym_id=gym_id,
                gym_name=gi.name.upper() if gi.name else None,
                area=gi.area,
                cover_pic=gi.cover_pic,
                distance_km=distance_km,
                dailypass_price=gi.dailypass_price,
                booking_count=facts.get("booking_count", 0),
                last_booked_days_ago=facts.get("last_booked_days_ago"),
            )
        except Exception:
            logging.exception("frequently_booked build failed for home/data")
            return None

    # ── Location resolution (cache hit / rebuild / stale fallback) ──

    async def _resolve_location(
        self, params: HomeDataParams, geo_hash: str, initial: dict,
    ) -> dict:

        cached = initial.get("location")
        if cached is not None:
            return cached

        got_lock = await self.home_repo.try_acquire_rebuild_lock(geo_hash)
        if got_lock:
            try:
                payload = await self._build_location_payload(params)
                await self.home_repo.cache_location(geo_hash, payload)
                return payload
            finally:
                await self.home_repo.release_rebuild_lock(geo_hash)

        stale = initial.get("location_stale")
        if stale is not None:
            return stale

        return await self._build_location_payload(params)

    # ── User state resolution (cache hit / rebuild) ────────────────

    async def _resolve_user_state(self, client_id: int, initial: dict) -> dict:
        """Return the per-user state, building if cache missed."""
        cached = initial.get("user_state")
        if cached is not None:
            return cached

        state = await self._build_user_state(client_id)
        # Fire-and-forget cache write — don't block response
        asyncio.create_task(
            self.home_repo.cache_user_state(client_id, state)
        )
        return state

    # ── Location payload builder (cache miss path) ─────────────────

    async def _build_location_payload(self, params: HomeDataParams) -> dict:
        """Build location-derived data — runs only on cache miss / stampede.

        Returns a JSON-serializable dict shared across all users in this geohash cell.
        Contains NO client-specific data — per-user offer overlay is applied later.
        """
        AsyncSessionLocal = get_async_sessionmaker()

        # ── Phase 1: Hydrate caches in parallel — each on its own session ──
        async def _hydrate_geo():
            async with AsyncSessionLocal() as session:
                await self.geo.hydrate(session)

        async def _hydrate_sessions():
            async with AsyncSessionLocal() as session:
                await SessionRepository(session, self.redis).hydrate()

        async def _hydrate_membership():
            async with AsyncSessionLocal() as session:
                await MembershipRepository(session, self.redis).hydrate()

        await asyncio.gather(
            _hydrate_geo(), _hydrate_sessions(), _hydrate_membership(),
        )

        # ── Phase 2: Nearby gyms ──
        distance_map = await self.geo.get_nearby_distances(
            params.client_lat, params.client_lng, NEARBY_RADIUS_KM,
        )
        nearby_ids = set(distance_map.keys())

        if not nearby_ids:
            return {
                "session_slots_base": [],
                "session_next_day": False,
                "nearby_memberships": [],
                "festival_offers": [],
                "dailypass_gyms": [],
            }

        # ── Phase 3: Pre-fetch shared gym data (own session, sequential — safe) ──
        all_gym_ids = list(nearby_ids)
        async with AsyncSessionLocal() as session:
            shared_repo = HomeRepository(session, self.redis)
            gyms_map = await shared_repo.fetch_gyms_cached(all_gym_ids)
            cover_pics = await shared_repo.fetch_cover_pics_cached(all_gym_ids)

        # ── Phase 4: Build sections in parallel — each on its own session ──
        async def _sessions_branch():
            async with AsyncSessionLocal() as session:
                return await self._build_nearby_sessions(
                    session, params, nearby_ids, distance_map, gyms_map, cover_pics,
                )

        async def _memberships_branch():
            async with AsyncSessionLocal() as session:
                return await self._build_nearby_memberships(
                    session, nearby_ids, distance_map, gyms_map, cover_pics,
                )

        # Festival offers temporarily disabled — always return an empty list.
        # async def _festival_branch():
        #     async with AsyncSessionLocal() as session:
        #         return await self._build_festival_offers(
        #             session, nearby_ids, distance_map, gyms_map, cover_pics,
        #         )

        async def _dailypass_branch():
            async with AsyncSessionLocal() as session:
                return await self._build_dailypass_gyms(session, params)

        sessions_payload, memberships_result, dailypass_result = await asyncio.gather(
            _sessions_branch(), _memberships_branch(), _dailypass_branch(),
        )
        festival_result: List[dict] = []  # festival offers disabled

        return {
            "session_slots_base": sessions_payload["slots"],
            "session_next_day": sessions_payload["next_day"],
            "nearby_memberships": memberships_result,
            "festival_offers": festival_result,
            "dailypass_gyms": dailypass_result,
        }

    # ── User-state builder (cache miss path) ───────────────────────

    async def _build_user_state(self, client_id: int) -> dict:
        """Build per-user time-sensitive state.

        All queries run in parallel with INDEPENDENT sessions (no shared self.db),
        so this is safe under asyncio.gather. Excludes dp_eligibility and
        first_time_user — those are computed fresh on every request.
        """
        (
            credits, profile, has_bookings, free_credits, ai_active,
            personal_coach_active, is_unlimited, reward_total_entries,
            friend_suggestions, frequently_booked,
        ) = await asyncio.gather(
            self.home_repo.fetch_credit_balance_isolated(client_id),
            self.home_repo.fetch_client_profile(client_id),
            self.home_repo.check_active_bookings(client_id),
            self.home_repo.fetch_free_credits_state(client_id),
            self.home_repo.fetch_active_ai_booking(client_id),
            self.home_repo.fetch_personal_coach_active(client_id),
            self.home_repo.fetch_unlimited_active(client_id),
            self.home_repo.fetch_reward_total_entries(client_id),
            self.home_repo.fetch_friend_suggestions(client_id),
            self.home_repo.fetch_last_dailypass_rebook(client_id),
        )
        return {
            "profile": profile,
            "credits": credits,
            "bookings": has_bookings,
            "free_credits": free_credits,
            "reward_total_entries": reward_total_entries,
            # True ⇒ user needs to (re)purchase: no active plan or it has expired.
            "ai": not ai_active,
            "personal_coach": not personal_coach_active,
            # True ⇒ active unlimited-scan pass (credit_999); scans are free.
            "is_unlimited": is_unlimited,
            # Suggested friends (serialized dicts) — cached with the rest of
            # user_state so they aren't re-computed on every home call.
            "friend_suggestions": friend_suggestions,
            # Rebook-card facts (last daily-pass gym_id + count + days-ago), or
            # None. Enriched with gym info + distance at response assembly.
            "frequently_booked": frequently_booked,
        }

    # ── Slot-2 promo rotation (the day's SECOND modal; slot 1 is gymmate) ──
    # Cycle: dailypass → rewards → refer → rewards1 → diet → workout → rewards2 → step → water → …
    # Advances once/day now (only slot 2 draws from it), so a full cycle ≈ 9 days.
    _PROMO_ROTATION = (
        "dailypass", "rewards", "refer", "rewards1", "diet", "workout",
        "rewards2", "step", "water",
    )

    @classmethod
    def _next_promo(cls, last: Optional[str]) -> str:
        """Return the next promo after `last` in the continuous rotation.

        Cycle: dailypass → rewards → refer → dailypass → …
        First-ever (last not in the cycle) starts at dailypass.
        """
        rot = cls._PROMO_ROTATION
        if last not in rot:
            return rot[0]
        return rot[(rot.index(last) + 1) % len(rot)]

    async def _resolve_promo_cards(
        self,
        client_id: int,
        nondp_shown_existed: bool,
        dp_shown_existed: bool,
        nondp_last: Optional[str],
        gymmate_last: Optional[str],
    ) -> Tuple[Dict[str, bool], bool, int, Optional[str], Optional[str]]:
        """Resolve the single modal to show this call. Two slots/day, max one
        modal per call:

          • Slot 1 (first home open of the day)  → ALWAYS a gymmate modal,
            alternating gymmate1 ⇄ gymmate2 by last-shown pointer.
          • Slot 2 (second open of the day)       → the existing 9-item rotation.

        Both slots are gated by their own once-per-day SET NX key, so the
        2-modals/day cap is preserved. Returns the chosen modal as the 5th
        element (gymmate modals aren't in `flags`, which only drives the
        legacy *_eligibility booleans for the 9-item promos).
        """
        flags: Dict[str, bool] = {p: False for p in self._PROMO_ROTATION}
        webinar_eligible = False
        referral_code: Optional[str] = None
        passes_left = 3

        today = datetime.now().date()
        modal: Optional[str] = None

        if not nondp_shown_existed:
            # ── Slot 1: gymmate, alternating by last-shown ──
            gymmate_next = "gymmate2" if gymmate_last == "gymmate1" else "gymmate1"
            if await self.home_repo.try_claim_nondp_slot(client_id, today, gymmate_next):
                modal = gymmate_next
                await self.home_repo.advance_gymmate_pointer(client_id, gymmate_next)
        elif not dp_shown_existed:
            # ── Slot 2: the 9-item rotation ──
            next_promo = self._next_promo(nondp_last)
            if await self.home_repo.check_and_mark_dp_shown_today(client_id, today):
                modal = next_promo
                await self.home_repo.advance_nondp_pointer(client_id, next_promo)
                if next_promo in flags:
                    flags[next_promo] = True
                if next_promo == "refer":
                    referral_code = await self.home_repo.fetch_referral_code(client_id)

        return flags, webinar_eligible, passes_left, referral_code, modal



    async def _build_nearby_sessions(
        self,
        db_session: AsyncSession,
        params: HomeDataParams,
        nearby_ids: Set[int],
        distance_map: Dict[int, float],
        gyms_map: Dict[int, dict],
        cover_pics: Dict[int, str],
    ) -> dict:

        sess_repo = SessionRepository(db_session, self.redis)
        home_repo = HomeRepository(db_session, self.redis)


        sess_enabled_ids = await sess_repo.get_session_enabled_gym_ids()
        session_candidates = nearby_ids & sess_enabled_ids

        if not session_candidates:
            return {"slots": [], "next_day": False}

        now = datetime.now()
        today = now.date()
        min_start = (now + timedelta(minutes=SLOT_LEAD_MINUTES)).time()

        schedules = await home_repo.fetch_today_schedules(
            session_candidates, today, min_start,
        )
        slot_date = today
        
        if not schedules:
            # Fallback: nothing left today → show tomorrow's slots (full day)
            tomorrow = today + timedelta(days=1)
           
            schedules = await home_repo.fetch_today_schedules(
                session_candidates, tomorrow, time(0, 0),
            )
            slot_date = tomorrow

        if not schedules:
            return {"slots": [], "next_day": False}

        # 3. Priority session IDs (yoga/pilates/zumba/PT)
        priority_ids = await home_repo.resolve_priority_session_ids()

        # Pick ONE slot per session_id — the nearest gym offering it
        best_per_session: Dict[int, object] = {}
        for sched in schedules:
            existing = best_per_session.get(sched.session_id)
            if existing is None or distance_map.get(sched.gym_id, 999) < distance_map.get(existing.gym_id, 999):
                best_per_session[sched.session_id] = sched

        # Sort: nearest distance first; same distance → earliest start time
        session_names_map = await home_repo.fetch_session_names(
            {s.session_id for s in best_per_session.values()}
        )

        def _session_sort_key(sched):
            return (sched.start_time, distance_map.get(sched.gym_id, 999))

        available_slots = sorted(best_per_session.values(), key=_session_sort_key)

        # 5. Pricing — bulk fetch (LOCATION-only data)
        slot_gym_ids = list({s.gym_id for s in available_slots})
        slot_session_ids = list({s.session_id for s in available_slots})

        # Fetch session settings — single bulk query (fixes N+1)
        raw_settings = await home_repo.fetch_bulk_session_settings(
            slot_gym_ids, slot_session_ids,
        )
        # Convert dicts to SimpleNamespace so .final_price works below
        settings_cache: Dict[Tuple[int, int], object] = {
            k: SimpleNamespace(**v) for k, v in raw_settings.items()
        }

        # 6. Build slot base data — store actual_price (owner final_price × commission)
        slots_out: List[dict] = []
        for sched in available_slots:
            gid = sched.gym_id
            gym = gyms_map.get(gid)
            if not gym:
                continue

            setting = settings_cache.get((gid, sched.session_id))
            actual_price = None
            if setting and setting.final_price:
                actual_price = compute_session_price_rupees(setting.final_price)

            raw_start = (
                sched.start_time.strftime("%H:%M")
                if hasattr(sched.start_time, "strftime")
                else str(sched.start_time)[:5]
            )
            raw_end = (
                sched.end_time.strftime("%H:%M")
                if hasattr(sched.end_time, "strftime")
                else str(sched.end_time)[:5]
            )

            distance = distance_map.get(gid)
            gym_name = gym["name"] if isinstance(gym, dict) else gym.name

            slots_out.append({
                "key": len(slots_out),
                "gym_id": gid,
                "gym_name": gym_name.upper() if gym_name else None,
                "distance_km": round(distance, 2) if distance is not None else None,
                "session_name": session_names_map.get(sched.session_id, ""),
                "session_id": sched.session_id,
                "schedule_id": sched.id,
                "trainer_id": sched.trainer_id,
                "date": slot_date.isoformat(),
                "start_time": to_12hr(raw_start),
                "end_time": to_12hr(raw_end),
                "actual_price": actual_price,
            })

        return {
            "slots": slots_out,
            "next_day": slot_date != today,
        }

  
    @staticmethod
    def _build_free_credits_card(
        free_credits_state: Optional[dict],
        balance: int,
        today,
    ) -> Optional[FreeCreditsCard]:
        """Compute the free-credits card from cached state.

        days_left is derived from ledger.expires_at so manual DB edits are honored.
        Falls back to granted_at + FREE_CREDITS_WINDOW_DAYS for legacy rows
        with NULL expires_at.

        Returns None when the card should be hidden:
          - User never received a signup_bonus (no ledger row).
          - User has any paid grant (purchase / subscription_bonus).
        Otherwise returns active or expired card.
        """
        if not free_credits_state:
            return None
        if free_credits_state.get("has_paid_plan"):
            return None

        expires_at_iso = free_credits_state.get("expires_at_iso")
        granted_at_iso = free_credits_state.get("granted_at_iso")

        expiry_date = None
        if expires_at_iso:
            try:
                expiry_date = datetime.fromisoformat(expires_at_iso).date()
            except ValueError:
                expiry_date = None

        if expiry_date is None and granted_at_iso:
            try:
                granted_at = datetime.fromisoformat(granted_at_iso)
                expiry_date = granted_at.date() + timedelta(days=FREE_CREDITS_WINDOW_DAYS)
            except ValueError:
                return None

        if expiry_date is None:
            return None

        days_left = max((expiry_date - today).days, 0)
        scans_left = max(int(balance or 0), 0)

        if days_left <= 0 or scans_left == 0:
            return FreeCreditsCard(state="expired", scans_left=0, days_left=0)
        return FreeCreditsCard(state="active", scans_left=scans_left, days_left=days_left)


    @staticmethod
    def _build_session_slots(slots_base: List[dict]) -> List[dict]:
        """Map location-cached session slots to response slots.

        The intro/gym session offer was removed, so price is just the cached
        actual_price (owner final_price × commission) and the offer flag is
        always False — no per-user overlay needed.
        """
        return [
            {
                "key": slot["key"],
                "gym_id": slot["gym_id"],
                "gym_name": slot["gym_name"],
                "distance_km": slot["distance_km"],
                "session_name": slot["session_name"],
                "session_id": slot["session_id"],
                "schedule_id": slot["schedule_id"],
                "trainer_id": slot["trainer_id"],
                "date": slot["date"],
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
                "price": slot.get("actual_price"),
                "session_offer_active": False,
            }
            for slot in slots_base
        ]

    # ── Section 2: Nearby Membership Gyms ─────────────────────────

    async def _build_nearby_memberships(
        self,
        db_session: AsyncSession,
        nearby_ids: Set[int],
        distance_map: Dict[int, float],
        gyms_map: Dict[int, dict],
        cover_pics: Dict[int, str],
    ) -> List[dict]:
        """Build nearby membership gyms — returns list of dicts (no client-specific data)."""
        mem_repo = MembershipRepository(db_session, self.redis)
        home_repo = HomeRepository(db_session, self.redis)

        # 1. Membership-enabled candidates
        mem_enabled_ids = await mem_repo.get_membership_enabled_gym_ids()
        mem_candidates = nearby_ids & mem_enabled_ids

        if not mem_candidates:
            return []

        # 2. Top 5 nearest — O(n) selection instead of O(n log n) full sort
        top5 = heapq.nsmallest(MEMBERSHIP_LIMIT, mem_candidates, key=lambda gid: distance_map.get(gid, 999))

        # 3. Fetch plans (cached 5min) + active offers
        plans_map = await home_repo.fetch_plans_cached(top5)
        all_offers_map = await mem_repo.fetch_active_offers(top5)

        # 4. Build response items (gym data already pre-fetched)
        results: List[dict] = []
        idx = 0
        for gid in top5:
            gym = gyms_map.get(gid)
            if not gym:
                continue

            plans = plans_map.get(gid, [])
            plan_result = _select_display_plan(plans, all_offers_map.get(gid, {}))
            per_month = None
            selected_plan_id = None
            selected_duration = None
            if plan_result:
                price, selected_plan_id, selected_duration = plan_result
                # Longest plan's commission-added total ÷ its duration.
                per_month = (
                    round(price / selected_duration) if selected_duration > 0 else price
                )

            distance = distance_map.get(gid)
            gym_name = gym["name"] if isinstance(gym, dict) else gym.name
            gym_area = gym["area"] if isinstance(gym, dict) else gym.area

            results.append({
                "key": idx,
                "gym_id": gid,
                "plan_id": selected_plan_id,
                "duration": selected_duration,
                "gym_name": gym_name.upper() if gym_name else None,
                "gym_area": gym_area,
                "cover_pic": cover_pics.get(gid, ""),
                "distance_km": round(distance, 2) if distance is not None else None,
                "per_month_price": per_month,
            })
            idx += 1

        return results

    # ── Section 3: Festival Offers (gyms with active membership offers) ──

    async def _build_festival_offers(
        self,
        db_session: AsyncSession,
        nearby_ids: Set[int],
        distance_map: Dict[int, float],
        gyms_map: Dict[int, dict],
        cover_pics: Dict[int, str],
    ) -> List[dict]:
        """Build festival offer gyms — returns list of dicts (no client-specific data)."""
        mem_repo = MembershipRepository(db_session, self.redis)
        home_repo = HomeRepository(db_session, self.redis)

        # 1. Membership-enabled nearby gyms
        mem_enabled_ids = await mem_repo.get_membership_enabled_gym_ids()
        mem_candidates = list(nearby_ids & mem_enabled_ids)

        if not mem_candidates:
            return []

        # 2. Fetch active offers — only gyms with offers qualify
        all_offers_map = await fetch_active_membership_offers(db_session, mem_candidates)
        offer_gym_ids = [gid for gid in mem_candidates if gid in all_offers_map]

        if not offer_gym_ids:
            return []

        # 3. Sort by distance
        offer_gym_ids.sort(key=lambda gid: distance_map.get(gid, 999))

        # 4. Fetch plans for offer gyms
        plans_map = await home_repo.fetch_plans_cached(offer_gym_ids)

        # 5. Build response
        multiplier = get_markup_multiplier()
        daily_discount = get_daily_offer_discount()
        results: List[dict] = []
        idx = 0

        for gid in offer_gym_ids:
            gym = gyms_map.get(gid)
            if not gym:
                continue

            plans = plans_map.get(gid, [])
            gym_offers = all_offers_map.get(gid, {})

            plan_result = _select_display_plan(plans, gym_offers)
            if not plan_result:
                continue

            offer_price, selected_plan_id, duration = plan_result

            # Original price without offer (for strikethrough)
            best = min(plans, key=lambda p: (p.get("duration", 0), p.get("amount", 0)))
            original_price = smart_round_price(best["amount"] * multiplier)
            if daily_discount > 0:
                original_price = max(original_price - daily_discount, 0)

            per_month = round(offer_price / duration) if duration > 0 else offer_price
            distance = distance_map.get(gid)
            gym_name = gym["name"] if isinstance(gym, dict) else gym.name

            results.append({
                "key": idx,
                "gym_id": gid,
                "plan_id": selected_plan_id,
                "gym_name": gym_name.upper() if gym_name else None,
                "cover_pic": cover_pics.get(gid, ""),
                "distance_km": round(distance, 2) if distance is not None else None,
                "offer_price": offer_price,
                "original_price": original_price,
                "duration": duration,
                "per_month_price": per_month,
            })
            idx += 1

        return results

    # ── Section 4: Nearby Daily Pass Gyms (top 5, same shape as /daily_pass/gyms) ──

    async def _build_dailypass_gyms(
        self, db_session: AsyncSession, params: HomeDataParams,
    ) -> List[dict]:

        from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.service import DailyPassService
        from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.schemas import DailyPassListParams

        # Test gym (id 1) must NEVER appear in the home dailypass section for any
        # user — it's only valid on the standalone /daily_pass page. It's excluded
        # explicitly here (not just via the test-client injection check below).
        HOME_DP_EXCLUDE_GYM_IDS = {1}

        dp_service = DailyPassService(db_session, self.redis)
        # NOTE: this payload is cached per-geohash and shared across ALL users in
        # the cell, so it must NOT carry client-specific logic. Passing the real
        # client_id would let the test-client (508) test-gym injection/forcing
        # (see DailyPassService.list_gyms) leak gym 1 into the shared cache and
        # surface it for every client. client_id=None makes those checks no-ops;
        # the listing is uniform anyway (no per-user pricing in this section).
        # We over-fetch (limit 6) so excluding gym 1 still leaves up to 5 gyms.
        dp_params = DailyPassListParams(
            client_lat=params.client_lat,
            client_lng=params.client_lng,
            client_id=None,
            page=1,
            limit=6,
        )
        result = await dp_service.list_gyms(dp_params)
        gyms = [
            g.model_dump()
            for g in result.data
            if g.gym_id not in HOME_DP_EXCLUDE_GYM_IDS
        ]
        return gyms[:5]

    # ── Nutrition Join ────────────────────────────────────────────

    async def check_nutrition_join(self, booking_id: int, client_id: int) -> NutritionJoinResponse:
        booking = await self.home_repo.get_active_nutrition_booking(booking_id, client_id)

        if not booking:
            raise FittbotHTTPException(
                status_code=404,
                detail="Booking not found or not accessible",
                error_code="NUTRITION_BOOKING_NOT_FOUND",
            )

        today = datetime.now().date()
        now = datetime.now().time()
        has_link = bool(booking.meeting_link and booking.meeting_link.strip())

        booking_date_str = booking.booking_date.isoformat()
        start_str = booking.start_time.strftime("%I:%M %p")
        end_str = booking.end_time.strftime("%I:%M %p")

        # Session expired
        if today > booking.booking_date or (today == booking.booking_date and now > booking.end_time):
            return NutritionJoinResponse(data=NutritionJoinData(
                join_time=False,
                meeting_link=has_link,
                session_expired=True,
                message="Session time has passed.",
                booking_date=booking_date_str,
                start_time=start_str,
                end_time=end_str,
            ))

        # Session not started yet
        if today < booking.booking_date or (today == booking.booking_date and now < booking.start_time):
            return NutritionJoinResponse(data=NutritionJoinData(
                join_time=False,
                meeting_link=has_link,
                message="Session has not started yet. Please join at the scheduled time.",
                booking_date=booking_date_str,
                start_time=start_str,
                end_time=end_str,
            ))

        # Within time window — can join
        return NutritionJoinResponse(data=NutritionJoinData(
            join_time=True,
            meeting_link=has_link,
            link=booking.meeting_link if has_link else None,
            message=None if has_link else "Meeting link not yet available. Please wait for the nutritionist to share the link.",
            booking_date=booking_date_str,
            start_time=start_str,
            end_time=end_str,
        ))
