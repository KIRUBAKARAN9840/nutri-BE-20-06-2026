"""Business logic for Personal Training gym listing.

Orchestrates shared services + PT-specific repository.
Finds gyms with session_id=2 enabled, resolves the best trainer per gym
(earliest-created with available slots), and returns slot timings + extra trainer count.
Dynamic pricing: ₹99 offer or actual markup price (same pattern as SessionService).
"""

import asyncio
from datetime import date, datetime
from typing import Dict, List, Optional, Set, Tuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.constants import GYM_OFFER_USER_CAP
from app.config.pricing import get_markup_multiplier
from app.models.async_database import get_async_sessionmaker

from ..shared.base_listing_service import BaseListingService, inject_test_gym
from ..shared.session_pricing_service import SessionPricingService
from ..shared.utils import to_12hr
from .repository import PERSONAL_TRAINING_SESSION_ID, SESSION_OFFER_PRICE, PTRepository
from .schemas import (
    PTGymResponse,
    PTListParams,
    PTListResponse,
    PTSlotItem,
    PTTrainerInfo,
    PTTrainerListItem,
    PTTrainerListResponse,
    PTTrainerSlotsResponse,
)


def _sort_key_for_slot(slot: PTSlotItem) -> str:
    """Return 24h start_time string for chronological sorting."""
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


class PTService(BaseListingService):
    """Personal Training listing: candidates → trainer resolution → slots → sort → paginate."""

    _error_code_prefix = "PT"

    def __init__(self, db: AsyncSession, redis: Redis):
        super().__init__(db, redis)
        self.pt_repo = PTRepository(db, redis)
        self.sess_pricing = SessionPricingService(db, redis)

    async def list_gyms(self, params: PTListParams) -> PTListResponse:

        # ── Parse & validate dates ───────────────────────────────
        today = date.today()
        target_dates = []
        for d in params.dates:
            parsed = datetime.strptime(d, "%Y-%m-%d").date()
            if parsed >= today:
                target_dates.append(parsed)

        if not target_dates:
            return self._empty_response(params)

        # ── Resolve client location ──────────────────────────────
        client_lat, client_lng = await self._resolve_client_location(
            params.client_id, params.client_lat, params.client_lng,
        )

        # ── Hydrate caches ───────────────────────────────────────
        AsyncSessionLocal = get_async_sessionmaker()

        async def _hydrate_pt():
            async with AsyncSessionLocal() as session:
                repo = PTRepository(session, self.redis)
                await repo.hydrate()

        await asyncio.gather(self._hydrate_geo(), _hydrate_pt())

        # ── 1. Candidate IDs: verified ∩ PT-enabled ─────────────
        verified_ids, pt_enabled_ids = await asyncio.gather(
            self.gym_repo.get_verified_gym_ids(),
            self.pt_repo.get_pt_enabled_gym_ids(),
        )
        candidate_ids = verified_ids & pt_enabled_ids

        if not candidate_ids:
            return self._empty_response(params)

        # ── 2. Apply session_low filter ──────────────────────────
        if params.session_low:
            low_ids = await self.pt_repo.get_pt_low_gym_ids(candidate_ids)
            if low_ids and params.client_id:
                user_booked = await self.pt_repo.fetch_user_booked_promo_gyms(
                    params.client_id, list(low_ids),
                )
                low_ids = low_ids - user_booked
            candidate_ids = candidate_ids & low_ids

        if not candidate_ids:
            return self._empty_response(params)

        # ── 3. Apply text filters ────────────────────────────────
        has_filters = any([
            params.search, params.city, params.area,
            params.pincode, params.state, params.session_low,
        ])
        candidate_ids = await self._apply_text_filters(
            candidate_ids,
            search=params.search, city=params.city,
            area=params.area, pincode=params.pincode,
            state=params.state,
        )

        if not candidate_ids:
            return self._empty_response(params)

        # ── 4. Fetch trainers + schedules ────────────────────────
        trainers_by_gym = await self.pt_repo.fetch_trainers_for_gyms(candidate_ids)
        all_schedules = await self.pt_repo.fetch_all_pt_schedules(candidate_ids)

        # Only keep gyms that actually have trainers
        candidate_ids = candidate_ids & set(trainers_by_gym.keys())
        if not candidate_ids or not all_schedules:
            return self._empty_response(params)

        # ── 5. Filter schedules per date ─────────────────────────
        per_date_schedules: Dict[date, Dict[int, Dict[int, List]]] = {}
        all_schedule_ids_set: Set[int] = set()

        for target_date in target_dates:
            gym_trainer_map = self.pt_repo.filter_schedules_for_date(all_schedules, target_date)
            if gym_trainer_map:
                per_date_schedules[target_date] = gym_trainer_map
                for trainer_map in gym_trainer_map.values():
                    for schedules in trainer_map.values():
                        for sched in schedules:
                            all_schedule_ids_set.add(sched.id)

        if not per_date_schedules:
            return self._empty_response(params)

        # ── 6. Build capacity map ────────────────────────────────
        all_schedule_ids = list(all_schedule_ids_set)
        capacity_map: Dict[int, int] = {}
        setting_ids_needed: Set[int] = set()

        for sched in all_schedules:
            if sched.id in all_schedule_ids_set:
                if sched.slot_quota is not None:
                    capacity_map[sched.id] = sched.slot_quota
                else:
                    setting_ids_needed.add(sched.id)

        if setting_ids_needed:
            needed_gym_ids = {s.gym_id for s in all_schedules if s.id in setting_ids_needed}
            settings_by_key = await self.pt_repo.fetch_capacity_settings(needed_gym_ids)
            for sched in all_schedules:
                if sched.id in setting_ids_needed:
                    setting = settings_by_key.get((sched.gym_id, sched.session_id, sched.trainer_id))
                    if setting is None and sched.trainer_id is not None:
                        setting = settings_by_key.get((sched.gym_id, sched.session_id))
                    capacity_map[sched.id] = (
                        setting.capacity if (setting and setting.capacity is not None) else 5
                    )

        # ── 7. Booking counts + availability ─────────────────────
        booking_counts = await self.pt_repo.get_multi_date_booking_counts(
            all_schedule_ids, target_dates,
        )
        user_offer = await self.pt_repo.get_user_offer_eligibility(params.client_id)

        # Build per-gym, per-trainer slot data across all dates
        # Structure: {gym_id: {trainer_id: {time_key: [(date, sched, available), ...]}}}
        gym_trainer_slots: Dict[int, Dict[int, Dict[tuple, List]]] = {}

        def _fmt_time(t) -> str:
            return t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)[:5]

        cap_get = capacity_map.get
        bc_get = booking_counts.get

        for target_date, gym_trainer_map in per_date_schedules.items():
            for gym_id, trainer_map in gym_trainer_map.items():
                gym_bucket = gym_trainer_slots.setdefault(gym_id, {})
                for trainer_id, schedules in trainer_map.items():
                    trainer_bucket = gym_bucket.setdefault(trainer_id, {})
                    for sched in schedules:
                        available = cap_get(sched.id, 5) - bc_get((sched.id, target_date), 0)
                        if available > 0:
                            time_key = (_fmt_time(sched.start_time), _fmt_time(sched.end_time))
                            trainer_bucket.setdefault(time_key, []).append(
                                (target_date, sched, available)
                            )

        # ── 8. Resolve best trainer per gym ──────────────────────
        # For each gym: iterate trainers by earliest-created order.
        # A trainer qualifies if they have common slots across ALL selected dates.
        # Pick first qualifying trainer; count remaining qualifying trainers.
        num_dates = len(target_dates)

        # {gym_id: (primary_trainer_profile, resolved_slots, extra_count)}
        gym_trainer_resolution: Dict[int, Tuple] = {}

        for gym_id in list(candidate_ids):
            trainers = trainers_by_gym.get(gym_id, [])
            trainer_slot_data = gym_trainer_slots.get(gym_id, {})

            qualifying_trainers: List[Tuple] = []  # (trainer_profile, slot_items)

            for tp in trainers:  # already ordered by created_at ASC
                tid = tp.trainer_id
                trainer_slots = trainer_slot_data.get(tid, {})
                if not trainer_slots:
                    continue

                # Only keep time slots present on ALL selected dates
                common_slots = {}
                for time_key, entries in trainer_slots.items():
                    dates_with_slot = {e[0] for e in entries}
                    if len(dates_with_slot) >= num_dates:
                        min_available = min(e[2] for e in entries)
                        sched = entries[0][1]  # representative schedule
                        common_slots[time_key] = (sched, min_available)

                if common_slots:
                    slot_items = []
                    for (raw_start, raw_end), (sched, avail) in common_slots.items():
                        slot_items.append(PTSlotItem(
                            schedule_id=sched.id,
                            start_time=to_12hr(raw_start),
                            end_time=to_12hr(raw_end),
                            available_slots=avail,
                        ))
                    slot_items.sort(key=_sort_key_for_slot)
                    qualifying_trainers.append((tp, slot_items))

            if not qualifying_trainers:
                candidate_ids.discard(gym_id)
                continue

            primary_trainer, primary_slots = qualifying_trainers[0]
            extra_count = len(qualifying_trainers) - 1
            gym_trainer_resolution[gym_id] = (primary_trainer, primary_slots, extra_count)

        if not candidate_ids:
            return self._empty_response(params)

        candidate_ids = inject_test_gym(candidate_ids, params.client_id)

        # ── 9. Sort by distance ──────────────────────────────────
        ordered_ids, distance_map = await self.geo.resolve_ordered_ids(
            candidate_ids, client_lat, client_lng, has_filters,
        )

        # ── 10. Price-based re-sorting ───────────────────────────
        # Pre-fetch shared pricing data once (used by both sort and response)
        _prefetched_pricing = None
        if params.sort_price and ordered_ids:
            _prefetched_pricing = await self._fetch_pricing_data(
                ordered_ids, params.client_id,
            )
            price_map = self._compute_pt_price_map(
                ordered_ids, gym_trainer_resolution, _prefetched_pricing,
                user_sess_eligible=user_offer["session_offer_eligible"],
                force_offer_price=params.session_low,
            )
            ordered_ids = self._price_sort(
                ordered_ids, price_map, distance_map,
                descending=params.sort_type == "descending",
            )

        # ── 11. Paginate ─────────────────────────────────────────
        page_ids, total_count, total_pages = self._paginate(
            ordered_ids, params.page, params.limit,
        )

        if not page_ids:
            return self._empty_response(params, total_count=total_count)

        # ── 12. Build responses ──────────────────────────────────
        gym_data = await self._build_responses(
            page_ids, distance_map, gym_trainer_resolution,
            user_sess_eligible=user_offer["session_offer_eligible"],
            force_offer_price=params.session_low,
            client_id=params.client_id,
            prefetched_pricing=_prefetched_pricing,
        )

        return PTListResponse(
            data=gym_data,
            session_name="Personal Training",
            session_offer_eligible=user_offer["session_offer_eligible"],
            session_count=user_offer["session_count"],
            client_name=user_offer["client_name"],
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages,
            ),
        )

    # ── Response builder ─────────────────────────────────────────

    async def _build_responses(
        self,
        gym_ids: List[int],
        distance_map: Dict[int, float],
        gym_trainer_resolution: Dict[int, Tuple],
        user_sess_eligible: bool = False,
        force_offer_price: bool = False,
        client_id: Optional[int] = None,
        prefetched_pricing: Optional[Dict] = None,
    ) -> List[PTGymResponse]:

        if prefetched_pricing:
            offer_map = prefetched_pricing["offer_map"]
            promo_counts = prefetched_pricing["promo_counts"]
            booked_gyms = prefetched_pricing["booked_gyms"]
            pt_settings = prefetched_pricing["pt_settings"]
        else:
            offer_map = await self.pt_repo.fetch_offer_flags(gym_ids)
            promo_counts = await self.pt_repo.fetch_promo_counts(gym_ids)
            booked_gyms = await self.pt_repo.fetch_user_booked_promo_gyms(client_id, gym_ids)
            pt_settings = await self.pt_repo.fetch_pt_settings(gym_ids)

        gyms_map, cover_pics, views_map, freq_booked_set = (
            await self._fetch_gym_data(gym_ids)
        )

        results: List[PTGymResponse] = []

        for gid in gym_ids:
            gym = gyms_map.get(gid)
            if not gym:
                continue

            resolution = gym_trainer_resolution.get(gid)
            if not resolution:
                continue

            primary_trainer, slot_items, extra_count = resolution

            # Resolve price from trainer-specific setting, fallback to gym-level
            setting = pt_settings.get((gid, primary_trainer.trainer_id))
            if not setting:
                setting = pt_settings.get((gid, None))

            actual_price = None
            if setting and setting.final_price:
                actual_price = round(setting.final_price * get_markup_multiplier())

            # Offer logic
            session_offer_active = SessionPricingService.is_offer_active(
                gid, offer_map, promo_counts, booked_gyms,
                user_sess_eligible=user_sess_eligible,
                force_offer_price=force_offer_price,
            )
            display_price = SESSION_OFFER_PRICE if session_offer_active else actual_price

            trainer_info = PTTrainerInfo(
                trainer_id=primary_trainer.trainer_id,
                name=primary_trainer.full_name,
                profile_image=primary_trainer.profile_image,
                experience=primary_trainer.experience,
                slots=slot_items,
            )

            distance = distance_map.get(gid)

            results.append(PTGymResponse(
                gym_id=gid,
                gym_name=gym.name.upper() if gym.name else None,
                cover_pic=cover_pics.get(gid, ""),
                area=gym.area,
                distance_km=round(distance, 2) if distance is not None else None,
                views=views_map.get(gid, 0),
                frequently_booked=gid in freq_booked_set,
                session_price=display_price,
                session_offer_active=session_offer_active,
                trainer=trainer_info,
                extra_trainers_count=extra_count,
            ))

        return results

    # ── Price map for sorting ────────────────────────────────────

    async def _fetch_pricing_data(
        self, gym_ids: List[int], client_id: Optional[int],
    ) -> Dict:
        """Fetch all pricing-related data. Returns a dict for reuse."""
        offer_map = await self.pt_repo.fetch_offer_flags(gym_ids)
        promo_counts = await self.pt_repo.fetch_promo_counts(gym_ids)
        booked_gyms = await self.pt_repo.fetch_user_booked_promo_gyms(client_id, gym_ids)
        pt_settings = await self.pt_repo.fetch_pt_settings(gym_ids)
        return {
            "offer_map": offer_map,
            "promo_counts": promo_counts,
            "booked_gyms": booked_gyms,
            "pt_settings": pt_settings,
        }

    def _compute_pt_price_map(
        self,
        gym_ids: List[int],
        gym_trainer_resolution: Dict[int, Tuple],
        pricing_data: Dict,
        user_sess_eligible: bool = False,
        force_offer_price: bool = False,
    ) -> Dict[int, float]:
        """Compute price map from pre-fetched pricing data (no I/O)."""
        offer_map = pricing_data["offer_map"]
        promo_counts = pricing_data["promo_counts"]
        booked_gyms = pricing_data["booked_gyms"]
        pt_settings = pricing_data["pt_settings"]

        price_map: Dict[int, float] = {}
        for gid in gym_ids:
            resolution = gym_trainer_resolution.get(gid)
            if not resolution:
                continue

            primary_trainer = resolution[0]
            setting = pt_settings.get((gid, primary_trainer.trainer_id))
            if not setting:
                setting = pt_settings.get((gid, None))
            if not setting or not setting.final_price:
                continue

            actual_price = round(setting.final_price * get_markup_multiplier())
            offer_active = SessionPricingService.is_offer_active(
                gid, offer_map, promo_counts, booked_gyms,
                user_sess_eligible=user_sess_eligible,
                force_offer_price=force_offer_price,
            )
            price_map[gid] = SESSION_OFFER_PRICE if offer_active else actual_price

        return price_map

    # ── Empty response helper ────────────────────────────────────

    def _empty_response(
        self, params: PTListParams, total_count: int = 0,
    ) -> PTListResponse:
        total_pages = (
            max(1, (total_count + params.limit - 1) // params.limit)
            if total_count else 0
        )
        return PTListResponse(
            data=[],
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages,
            ),
        )

    # ── Other trainers at a gym ───────────────────────────────────

    async def get_trainers(
        self, gym_id: int, exclude_trainer_id: int,
    ) -> PTTrainerListResponse:
        """List other trainers at a gym, excluding the primary one shown in /gyms."""
        trainers_by_gym = await self.pt_repo.fetch_trainers_for_gyms({gym_id})
        trainers = trainers_by_gym.get(gym_id, [])

        items = [
            PTTrainerListItem(
                trainer_id=tp.trainer_id,
                name=tp.full_name,
                profile_image=tp.profile_image,
                experience=tp.experience,
            )
            for tp in trainers
            if tp.trainer_id != exclude_trainer_id
        ]

        return PTTrainerListResponse(gym_id=gym_id, trainers=items)

    # ── Slots for a specific trainer ──────────────────────────────

    async def get_trainer_slots(
        self, gym_id: int, trainer_id: int,
        dates: List[str], client_id: Optional[int] = None,
    ) -> PTTrainerSlotsResponse:
        """Get available slots for a specific trainer — same format as /gyms."""

        today = date.today()
        target_dates = []
        for d in dates:
            parsed = datetime.strptime(d, "%Y-%m-%d").date()
            if parsed >= today:
                target_dates.append(parsed)

        if not target_dates:
            return PTTrainerSlotsResponse(
                gym_id=gym_id,
                trainer=PTTrainerInfo(trainer_id=trainer_id, name=""),
            )

        # Fetch trainer profile
        trainers_by_gym = await self.pt_repo.fetch_trainers_for_gyms({gym_id})
        trainers = trainers_by_gym.get(gym_id, [])
        trainer_profile = None
        for tp in trainers:
            if tp.trainer_id == trainer_id:
                trainer_profile = tp
                break

        if not trainer_profile:
            return PTTrainerSlotsResponse(
                gym_id=gym_id,
                trainer=PTTrainerInfo(trainer_id=trainer_id, name=""),
            )

        # Fetch schedules & filter for this trainer
        all_schedules = await self.pt_repo.fetch_all_pt_schedules({gym_id})

        per_date_schedules: Dict[date, List] = {}
        all_schedule_ids_set: Set[int] = set()

        for target_date in target_dates:
            gym_trainer_map = self.pt_repo.filter_schedules_for_date(all_schedules, target_date)
            trainer_scheds = gym_trainer_map.get(gym_id, {}).get(trainer_id, [])
            if trainer_scheds:
                per_date_schedules[target_date] = trainer_scheds
                for sched in trainer_scheds:
                    all_schedule_ids_set.add(sched.id)

        if not per_date_schedules:
            return PTTrainerSlotsResponse(
                gym_id=gym_id,
                trainer=PTTrainerInfo(
                    trainer_id=trainer_id,
                    name=trainer_profile.full_name,
                    profile_image=trainer_profile.profile_image,
                    experience=trainer_profile.experience,
                ),
            )

        # Build capacity map
        all_schedule_ids = list(all_schedule_ids_set)
        capacity_map: Dict[int, int] = {}
        setting_ids_needed: Set[int] = set()

        for sched in all_schedules:
            if sched.id in all_schedule_ids_set:
                if sched.slot_quota is not None:
                    capacity_map[sched.id] = sched.slot_quota
                else:
                    setting_ids_needed.add(sched.id)

        if setting_ids_needed:
            settings_by_key = await self.pt_repo.fetch_capacity_settings({gym_id})
            for sched in all_schedules:
                if sched.id in setting_ids_needed:
                    setting = settings_by_key.get((sched.gym_id, sched.session_id, sched.trainer_id))
                    if setting is None and sched.trainer_id is not None:
                        setting = settings_by_key.get((sched.gym_id, sched.session_id))
                    capacity_map[sched.id] = (
                        setting.capacity if (setting and setting.capacity is not None) else 5
                    )

        # Booking counts
        booking_counts = await self.pt_repo.get_multi_date_booking_counts(
            all_schedule_ids, target_dates,
        )

        # Build slot data across dates
        def _fmt_time(t) -> str:
            return t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)[:5]

        num_dates = len(target_dates)
        trainer_slots: Dict[tuple, List] = {}

        for target_date, schedules in per_date_schedules.items():
            for sched in schedules:
                available = capacity_map.get(sched.id, 5) - booking_counts.get((sched.id, target_date), 0)
                if available > 0:
                    time_key = (_fmt_time(sched.start_time), _fmt_time(sched.end_time))
                    trainer_slots.setdefault(time_key, []).append(
                        (target_date, sched, available)
                    )

        # Only keep slots present on ALL selected dates
        slot_items = []
        for time_key, entries in trainer_slots.items():
            dates_with_slot = {e[0] for e in entries}
            if len(dates_with_slot) >= num_dates:
                min_available = min(e[2] for e in entries)
                sched = entries[0][1]
                slot_items.append(PTSlotItem(
                    schedule_id=sched.id,
                    start_time=to_12hr(time_key[0]),
                    end_time=to_12hr(time_key[1]),
                    available_slots=min_available,
                ))
        slot_items.sort(key=_sort_key_for_slot)

        # Pricing
        pt_settings = await self.pt_repo.fetch_pt_settings([gym_id])
        setting = pt_settings.get((gym_id, trainer_id))
        if not setting:
            setting = pt_settings.get((gym_id, None))

        actual_price = None
        if setting and setting.final_price:
            actual_price = round(setting.final_price * get_markup_multiplier())

        user_offer = await self.pt_repo.get_user_offer_eligibility(client_id)
        offer_map = await self.pt_repo.fetch_offer_flags([gym_id])
        promo_counts = await self.pt_repo.fetch_promo_counts([gym_id])
        booked_gyms = await self.pt_repo.fetch_user_booked_promo_gyms(client_id, [gym_id])

        session_offer_active = SessionPricingService.is_offer_active(
            gym_id, offer_map, promo_counts, booked_gyms,
            user_sess_eligible=user_offer["session_offer_eligible"],
        )
        display_price = SESSION_OFFER_PRICE if session_offer_active else actual_price

        return PTTrainerSlotsResponse(
            gym_id=gym_id,
            trainer=PTTrainerInfo(
                trainer_id=trainer_id,
                name=trainer_profile.full_name,
                profile_image=trainer_profile.profile_image,
                experience=trainer_profile.experience,
                slots=slot_items,
            ),
            session_price=display_price,
            session_offer_active=session_offer_active,
        )
