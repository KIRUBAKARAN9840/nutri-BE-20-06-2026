"""Business logic for Session gym listing.

Orchestrates shared services + session-specific repository.
Filters by session type + date, shows only gyms with non-expired available slots.
Dynamic pricing: ₹99 offer or actual session price (same pattern as DailyPassService).
"""

import asyncio
from datetime import date, datetime
from typing import Dict, List, Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.pricing import get_markup_multiplier, compute_session_price_rupees
from app.models.async_database import get_async_sessionmaker

from ..shared.base_listing_service import BaseListingService, inject_test_gym
from ..shared.session_pricing_service import SessionPricingService
from ..shared.utils import to_12hr
from .repository import SESSION_OFFER_PRICE, SessionRepository
from .schemas import (
    SessionGymResponse,
    SessionListParams,
    SessionListResponse,
    SessionSlotItem,
)


def _sort_key_for_slot(slot: "SessionSlotItem") -> str:
    """Return 24h start_time string for sorting (extract from AM/PM)."""
    parts = slot.start_time.split()
    time_part = parts[0]
    period = parts[1] if len(parts) > 1 else ""
    h, m = time_part.split(":")
    h = int(h)
    if period == "AM" and h == 12:
        h = 0
    elif period == "PM" and h != 12:
        h += 12
    return f"{h:02d}:{m}"


class SessionService(BaseListingService):
    """Session gym listing: candidate resolution → filter by date/slots → sort → paginate → enrich."""

    _error_code_prefix = "SESSION"

    def __init__(self, db: AsyncSession, redis: Redis):
        super().__init__(db, redis)
        self.sess_repo = SessionRepository(db, redis)
        self.sess_pricing = SessionPricingService(db, redis)

    async def list_gyms(self, params: SessionListParams) -> SessionListResponse:

        # Parse and validate dates — drop any past dates
        today = date.today()
        target_dates = []
        for d in params.dates:
            parsed = datetime.strptime(d, "%Y-%m-%d").date()
            if parsed >= today:
                target_dates.append(parsed)

        if not target_dates:
            return self._empty_response(params)

        # Resolve client location (cache or fallback)
        client_lat, client_lng = await self._resolve_client_location(
            params.client_id, params.client_lat, params.client_lng
        )

        # Hydrate caches (lock-guarded, skips if fresh)
        AsyncSessionLocal = get_async_sessionmaker()

        async def _hydrate_sessions():
            async with AsyncSessionLocal() as session:
                repo = SessionRepository(session, self.redis)
                await repo.hydrate()

        await asyncio.gather(self._hydrate_geo(), _hydrate_sessions())

        # 1. Get candidate IDs: verified ∩ session-enabled
        verified_ids, sess_enabled_ids = await asyncio.gather(
            self.gym_repo.get_verified_gym_ids(),
            self.sess_repo.get_session_enabled_gym_ids(),
        )
        candidate_ids = verified_ids & sess_enabled_ids

        if not candidate_ids:
            return self._empty_response(params)

        # 2. Apply session_low filter (only gyms with offer + under 50 cap + user not already booked)
        if params.session_low:
            low_ids = await self.sess_repo.get_session_low_gym_ids(candidate_ids)
            if low_ids and params.client_id:
                user_booked = await self.sess_repo.fetch_user_booked_promo_gyms(
                    params.client_id, list(low_ids)
                )
                low_ids = low_ids - user_booked
            candidate_ids = candidate_ids & low_ids

        if not candidate_ids:
            return self._empty_response(params)

        # 3. Apply text filters
        has_filters = any([
            params.search, params.city, params.area, params.pincode,
            params.state, params.session_low,
        ])
        candidate_ids = await self._apply_text_filters(
            candidate_ids,
            search=params.search,
            city=params.city,
            area=params.area,
            pincode=params.pincode,
            state=params.state,
        )

        if not candidate_ids:
            return self._empty_response(params)

        # 5. Filter by dates + session: only keep TIME SLOTS available on ALL dates
        # Fetch all schedules ONCE (single DB query), then filter per date in Python
        all_schedules = await self.sess_repo.fetch_all_schedules(
            params.session_id, candidate_ids
        )

        if not all_schedules:
            return self._empty_response(params)

        # Filter schedules per date (pure Python, no DB)
        per_date_schedules: Dict[date, Dict[int, List]] = {}
        all_schedule_ids_set = set()
        for target_date in target_dates:
            gym_schedules_map = self.sess_repo.filter_schedules_for_date(
                all_schedules, target_date
            )
            if gym_schedules_map:
                per_date_schedules[target_date] = gym_schedules_map
                for schedules in gym_schedules_map.values():
                    for sched in schedules:
                        all_schedule_ids_set.add(sched.id)

        if not per_date_schedules:
            return self._empty_response(params)

        # Fetch booking counts + capacity for ALL schedules × ALL dates in ONE query each
        all_schedule_ids = list(all_schedule_ids_set)

        # Build capacity map from schedules (same logic as get_slot_availability but done once)
        capacity_map: Dict[int, int] = {}
        setting_ids_needed = set()
        for sched in all_schedules:
            if sched.id in all_schedule_ids_set:
                if sched.slot_quota is not None:
                    capacity_map[sched.id] = sched.slot_quota
                else:
                    setting_ids_needed.add(sched.id)

        # Sequential DB queries (same session can't run concurrently)
        if setting_ids_needed:
            needed_gym_ids = {s.gym_id for s in all_schedules if s.id in setting_ids_needed}
            settings_by_key = await self.sess_repo.fetch_capacity_settings(needed_gym_ids)
            for sched in all_schedules:
                if sched.id in setting_ids_needed:
                    setting = settings_by_key.get((sched.gym_id, sched.session_id, sched.trainer_id))
                    if setting is None and sched.trainer_id is not None:
                        setting = settings_by_key.get((sched.gym_id, sched.session_id))
                    capacity_map[sched.id] = setting.capacity if (setting and setting.capacity is not None) else 999

        booking_counts = await self.sess_repo.get_multi_date_booking_counts(all_schedule_ids, target_dates)
        user_offer = await self.sess_repo.get_user_offer_eligibility(params.client_id)

        # Build availability per (schedule_id, date) and collect gym_date_slots
        availability_map: Dict[int, int] = {}
        gym_date_slots: Dict[int, Dict] = {}
        num_dates = len(target_dates)

        for target_date, gym_schedules_map in per_date_schedules.items():
            for gym_id, schedules in gym_schedules_map.items():
                for sched in schedules:
                    cap = capacity_map.get(sched.id, 999)
                    booked = booking_counts.get((sched.id, target_date), 0)
                    available = max(0, cap - booked)
                    availability_map[sched.id] = max(availability_map.get(sched.id, 0), available)

                    if available > 0:
                        time_key = (str(sched.start_time)[:5], str(sched.end_time)[:5])
                        gym_date_slots.setdefault(gym_id, {}).setdefault(target_date, {})[time_key] = (sched, available)

        # Only keep time slots common across ALL selected dates
        gyms_with_slots: Dict[int, List] = {}
        for gym_id, date_map in gym_date_slots.items():
            if len(date_map) < num_dates:
                continue
            all_time_keys = [set(slots.keys()) for slots in date_map.values()]
            common_time_keys = all_time_keys[0]
            for tk in all_time_keys[1:]:
                common_time_keys = common_time_keys & tk
            if not common_time_keys:
                continue
            for td, slots in date_map.items():
                for time_key in common_time_keys:
                    sched, _ = slots[time_key]
                    gyms_with_slots.setdefault(gym_id, []).append((td, sched))

        candidate_ids = candidate_ids & set(gyms_with_slots.keys())

        if not candidate_ids:
            return self._empty_response(params)

        candidate_ids = inject_test_gym(candidate_ids, params.client_id)

        # 6. Sort by distance
        ordered_ids, distance_map = await self.geo.resolve_ordered_ids(
            candidate_ids, client_lat, client_lng, has_filters
        )

        if params.client_id == 508 and 1 not in ordered_ids:
            ordered_ids = [1] + list(ordered_ids)

        # 9. Price-based re-sorting
        if params.sort_price and ordered_ids:
            price_map = await self.sess_pricing.build_price_sort_map(
                ordered_ids, params.session_id,
                user_sess_eligible=user_offer["session_offer_eligible"],
                force_offer_price=params.session_low,
                client_id=params.client_id,
            )
            ordered_ids = self._price_sort(
                ordered_ids, price_map, distance_map,
                descending=params.sort_type == "descending",
            )

        # 10. Paginate
        page_ids, total_count, total_pages = self._paginate(
            ordered_ids, params.page, params.limit
        )

        if not page_ids:
            return self._empty_response(params, total_count=total_count)

        # 11. Get session name + bulk fetch
        session_name = await self.sess_repo.get_session_name(params.session_id)
        gym_data = await self._build_responses(
            page_ids, distance_map, gyms_with_slots, availability_map,
            session_id=params.session_id,
            user_sess_eligible=user_offer["session_offer_eligible"],
            force_offer_price=params.session_low,
            client_id=params.client_id,
        )

        # 13. Return
        return SessionListResponse(
            data=gym_data,
            session_name=session_name,
            session_offer_eligible=user_offer["session_offer_eligible"],
            session_count=user_offer["session_count"],
            client_name=user_offer["client_name"],
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages
            ),
        )

    async def _build_responses(
        self, gym_ids: List[int], distance_map: Dict[int, float],
        gyms_with_slots: Dict[int, List], availability_map: Dict[int, int],
        session_id: int,
        user_sess_eligible: bool = False,
        force_offer_price: bool = False,
        client_id: Optional[int] = None,
    ) -> List[SessionGymResponse]:
        """Sequential bulk fetch (same DB session can't run concurrent queries).
        Redis caching on most methods makes 2nd+ requests fast."""

        gyms_map, cover_pics, views_map, freq_booked_set = (
            await self._fetch_gym_data(gym_ids)
        )
        settings_map = await self.sess_repo.fetch_session_settings(gym_ids, session_id)
        offer_map = await self.sess_repo.fetch_offer_flags(gym_ids)
        promo_counts = await self.sess_repo.fetch_promo_counts(gym_ids)
        booked_gyms = await self.sess_repo.fetch_user_booked_promo_gyms(client_id, gym_ids)

        results: List[SessionGymResponse] = []

        for gid in gym_ids:
            gym = gyms_map.get(gid)
            if not gym:
                continue

            setting = settings_map.get(gid)
            actual_price = None
            if setting and setting.final_price:
                actual_price = compute_session_price_rupees(setting.final_price)

            # Session offer logic (delegated to shared service)
            session_offer_active = SessionPricingService.is_offer_active(
                gid, offer_map, promo_counts, booked_gyms,
                user_sess_eligible=user_sess_eligible,
                force_offer_price=force_offer_price,
            )

            display_price = SESSION_OFFER_PRICE if session_offer_active else actual_price

            # Build unique time slots (deduplicate across dates — user books the same slot on all dates)
            date_schedules = gyms_with_slots.get(gid, [])
            time_slot_map: Dict[tuple, dict] = {}
            for slot_date, sched in date_schedules:
                available = availability_map.get(sched.id, 0)
                if available > 0:
                    raw_start = sched.start_time.strftime("%H:%M") if hasattr(sched.start_time, 'strftime') else str(sched.start_time)[:5]
                    raw_end = sched.end_time.strftime("%H:%M") if hasattr(sched.end_time, 'strftime') else str(sched.end_time)[:5]
                    time_key = (raw_start, raw_end)
                    if time_key not in time_slot_map:
                        time_slot_map[time_key] = {"schedule_id": sched.id, "available": available}
                    else:
                        time_slot_map[time_key]["available"] = min(time_slot_map[time_key]["available"], available)

            slot_items = []
            for (raw_start, raw_end), info in time_slot_map.items():
                slot_items.append(
                    SessionSlotItem(
                        schedule_id=info["schedule_id"],
                        start_time=to_12hr(raw_start),
                        end_time=to_12hr(raw_end),
                        available_slots=info["available"],
                    )
                )

            # Sort slots by start_time (AM → PM)
            slot_items.sort(key=_sort_key_for_slot)

            distance = distance_map.get(gid)

            results.append(
                SessionGymResponse(
                    gym_id=gid,
                    gym_name=gym.name.upper() if gym.name else None,
                    cover_pic=cover_pics.get(gid, ""),
                    area=gym.area,
                    distance_km=round(distance, 2) if distance is not None else None,
                    views=views_map.get(gid, 0),
                    frequently_booked=gid in freq_booked_set,
                    session_price=display_price,
                    session_offer_active=session_offer_active,
                    slots=slot_items,
                )
            )

        return results

    def _empty_response(self, params: SessionListParams, total_count: int = 0) -> SessionListResponse:
        total_pages = max(1, (total_count + params.limit - 1) // params.limit) if total_count else 0
        return SessionListResponse(
            data=[],
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages
            ),
        )
