"""Business logic for Gym Membership listing + gym details.

Orchestrates shared services + membership-specific repository.
Plan selection priority (listing):
  1. Sort by duration ASC (1-month first)
  2. Within same duration, prefer: individual > personal > couple > buddy
  3. Pick the first match -- show its per-month price
  4. If no plans at all, gym is excluded from listing
"""

import asyncio
import json
import os
from typing import Dict, List, Optional, Tuple

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.pricing import (
    get_daily_offer_discount, get_markup_multiplier,
    get_walkaway_visited_key, get_walkaway_redis_key,
    get_seconds_until_midnight_ist, apply_walkaway_discount,
)
from app.models.async_database import get_async_sessionmaker
from app.models.fittbot_models import (
    Gym, GymLocation, GymMembershipOffer, GymPlans, GymStudiosPic, NoCostEmi,
)

from ..shared.base_listing_service import BaseListingService, inject_test_gym
from ..shared.utils import resolve_offer_base_amount, smart_round_price
from .repository import MembershipRepository
from .schemas import (
    GymAddress,
    GymDetailsData,
    GymDetailsResponse,
    GymPhotoItem,
    MembershipGymResponse,
    MembershipListParams,
    MembershipListResponse,
    PlanItem,
)

# Plan-for priority: lower = preferred
_PLAN_TYPE_PRIORITY = {
    "individual": 0,
    None: 0,
    "": 0,
    "personal": 1,
    "couple": 2,
    "buddy": 3,
}

BASE_FITTBOT_MONTHLY = 398


def _safe_json_struct(value):
    """Coerce a DB column to a list/dict/None — never a raw string/number.
    Frontend expects an iterable or null; bad JSON or wrong shapes become None.
    """
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, (list, dict)) else None
    return None


def _plan_matches_membership_types(plan: GymPlans, membership_types: List[str]) -> bool:
    """Check if a plan matches any of the given membership_types."""
    for mt in membership_types:
        if mt == "membership":
            if not plan.personal_training and (plan.plan_for is None or plan.plan_for not in ["couple", "buddy"]):
                return True
        elif mt == "pt":
            if plan.personal_training and (plan.plan_for is None or plan.plan_for not in ["couple", "buddy"]):
                return True
        elif mt == "couple_membership":
            if not plan.personal_training and plan.plan_for == "couple":
                return True
        elif mt == "couple_pt":
            if plan.personal_training and plan.plan_for == "couple":
                return True
        elif mt == "buddy":
            if not plan.personal_training and plan.plan_for == "buddy":
                return True
        elif mt == "buddy_pt":
            if plan.personal_training and plan.plan_for == "buddy":
                return True
    return False


def _plan_sort_key(plan: GymPlans) -> tuple:
    """Sort key: (duration ASC, type_priority ASC)."""
    if plan.personal_training:
        if plan.plan_for == "couple":
            type_prio = 2
        elif plan.plan_for == "buddy":
            type_prio = 3
        else:
            type_prio = 1
    else:
        type_prio = _PLAN_TYPE_PRIORITY.get(plan.plan_for, 0)
    return (plan.duration, type_prio)


def _get_display_price(plan: GymPlans) -> int:
    """Return the total display price for the selected plan (same as gym_details)."""
    multiplier = get_markup_multiplier()
    return smart_round_price(plan.amount * multiplier)


def _resolve_base_amount(plan, gym_offers: dict) -> tuple:
    """Wrapper around shared resolve_offer_base_amount for plan objects."""
    return resolve_offer_base_amount(plan.id, plan.amount, gym_offers)


def _calculate_nutritional_plan(duration: int) -> Optional[dict]:
    if duration>=12:
        return {"consultations": 3, "amount": 3600}
    elif duration >=6:
        return {"consultations": 2, "amount": 2400}
    elif duration <= 5:
        return {"consultations": 1, "amount": 1200}
    return None


def _calculate_fittbot_plan_offer(duration: int) -> dict:
    return {
        "fittbot_plan": {
            "duration": duration,
            "price_rupees": duration * BASE_FITTBOT_MONTHLY,
        },
        "can_offer_fittbot_plan": True,
    }


class MembershipService(BaseListingService):
    """Membership gym listing + gym details."""

    _error_code_prefix = "MEM"

    def __init__(self, db: AsyncSession, redis: Redis):
        super().__init__(db, redis)
        self.mem_repo = MembershipRepository(db, redis)

    # =====================================================================
    #  /gyms  -- listing
    # =====================================================================

    async def list_gyms(self, params: MembershipListParams, client_id: int = None) -> MembershipListResponse:

        # Bypass fittbot_verified gate for whitelisted client_id (env-driven).
        # UNIQUE_CLIENT_ID can be a single id or comma-separated list.
        show_all_gyms = False
        unique_env = os.getenv("UNIQUE_CLIENT_ID")
        if unique_env and client_id is not None:
            allowed = {s.strip() for s in unique_env.split(",") if s.strip()}
            if str(client_id) in allowed:
                show_all_gyms = True

        walkaway_active = False
        
        if client_id:
            walkaway_key = get_walkaway_redis_key(client_id)
            walkaway_active = bool(await self.redis.exists(walkaway_key))

            if not walkaway_active:
                visited_key = get_walkaway_visited_key(client_id)
                if bool(await self.redis.exists(visited_key)):
                    ttl = get_seconds_until_midnight_ist()
                    await self.redis.set(walkaway_key, "1", ex=ttl)
                    walkaway_active = True

        client_lat, client_lng = await self._resolve_client_location(
            params.client_id, params.client_lat, params.client_lng
        )

        AsyncSessionLocal = get_async_sessionmaker()

        async def _hydrate_membership():
            async with AsyncSessionLocal() as session:
                repo = MembershipRepository(session, self.redis)
                await repo.hydrate()

        await asyncio.gather(self._hydrate_geo(), _hydrate_membership())

        if show_all_gyms:
            all_ids = await self.gym_repo.get_all_gym_ids()
            verified_only_ids = await self.gym_repo.get_verified_gym_ids()
            verified_ids = all_ids
        else:
            verified_ids = await self.gym_repo.get_verified_gym_ids()
            verified_only_ids = verified_ids
        mem_enabled_ids = await self.mem_repo.get_membership_enabled_gym_ids()
        candidate_ids = verified_ids & mem_enabled_ids

        if not candidate_ids:
            return self._empty_response(params)

        candidate_ids = await self._apply_fitness_filter(
            candidate_ids, params.fitness_types, include_unverified=show_all_gyms,
        )
        if not candidate_ids:
            return self._empty_response(params)

        # no_cost_emi filter: gyms with EMI enabled AND at least one plan >= 4000
        if params.no_cost_emi:
            candidate_ids = await self.mem_repo.filter_by_no_cost_emi(candidate_ids)
            if not candidate_ids:
                return self._empty_response(params)

        # membership_types filter: membership, pt, couple_membership, couple_pt, buddy, buddy_pt
        if params.membership_types:
            candidate_ids = await self.mem_repo.filter_by_membership_types(
                candidate_ids, params.membership_types,
            )
            if not candidate_ids:
                return self._empty_response(params)

        has_filters = any([
            params.search, params.city, params.area,
            params.pincode, params.state, params.fitness_types,
            params.no_cost_emi, params.membership_types,
        ])
        candidate_ids = await self._apply_text_filters(
            candidate_ids,
            search=params.search, city=params.city, area=params.area,
            pincode=params.pincode, state=params.state,
            include_unverified=show_all_gyms,
        )
        if not candidate_ids:
            return self._empty_response(params)

        candidate_ids = inject_test_gym(candidate_ids, params.client_id)

        ordered_ids, distance_map = await self.geo.resolve_ordered_ids(
            candidate_ids, client_lat, client_lng, has_filters or show_all_gyms,
        )

        # Whitelist mode: surface unverified gyms first so they appear on page 1.
        if show_all_gyms:
            unverified_set = set(candidate_ids) - verified_only_ids
            if unverified_set:
                unverified_in_order = [g for g in ordered_ids if g in unverified_set]
                verified_in_order = [g for g in ordered_ids if g not in unverified_set]
                ordered_ids = unverified_in_order + verified_in_order

        if params.client_id == 508 and 1 not in ordered_ids:
            ordered_ids = [1] + list(ordered_ids)

        # Fetch plans + active offers for all candidates
        all_plans_map = await self.mem_repo.fetch_plans_for_gyms(list(ordered_ids))
        all_offers_map = await self.mem_repo.fetch_active_offers(list(ordered_ids))

        # Build display price + plan_id + duration maps
        display_price_map: Dict[int, int] = {}
        display_original_price_map: Dict[int, int] = {}
        display_plan_id_map: Dict[int, int] = {}
        display_duration_map: Dict[int, int] = {}
        display_offer_active_map: Dict[int, bool] = {}
        for gid in ordered_ids:
            result = self._select_display_plan(
                all_plans_map.get(gid, []), params.membership_types,
                all_offers_map.get(gid, {}),
            )
            if result is not None:
                display_price_map[gid] = result[0]
                display_plan_id_map[gid] = result[1]
                display_duration_map[gid] = result[2]
                display_offer_active_map[gid] = result[3]
                display_original_price_map[gid] = result[4]

        # Remove gyms with no displayable price
        ordered_ids = [gid for gid in ordered_ids if gid in display_price_map]

        # Apply walkaway 5% discount to listing prices
        if walkaway_active:
            for gid in ordered_ids:
                if gid in display_price_map:
                    display_price_map[gid] = apply_walkaway_discount(display_price_map[gid])

        if params.sort_price and ordered_ids:
            ordered_ids = self._price_sort(
                ordered_ids, display_price_map, distance_map,
                descending=params.sort_type == "descending",
            )

        page_ids, total_count, total_pages = self._paginate(
            ordered_ids, params.page, params.limit
        )
        if not page_ids:
            return self._empty_response(params, total_count=total_count)

        gym_data = await self._build_responses(
            page_ids, distance_map, display_price_map,
            display_plan_id_map, display_duration_map,
            display_offer_active_map, display_original_price_map,
        )

        return MembershipListResponse(
            data=gym_data,
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages
            ),
            walkaway_discount_active=walkaway_active,
        )

    async def _build_responses(
        self, gym_ids: List[int], distance_map: Dict[int, float],
        display_price_map: Dict[int, int], display_plan_id_map: Dict[int, int],
        display_duration_map: Dict[int, int],
        display_offer_active_map: Dict[int, bool] = None,
        display_original_price_map: Dict[int, int] = None,
    ) -> List[MembershipGymResponse]:

        gyms_map, cover_pics, views_map, freq_booked_set = (
            await self._fetch_gym_data(gym_ids)
        )
        emi_map = await self.mem_repo.fetch_emi_flags(gym_ids)

        results: List[MembershipGymResponse] = []
        for gid in gym_ids:
            gym = gyms_map.get(gid)
            if not gym:
                continue
            price = display_price_map.get(gid)
            if price is None:
                continue
            distance = distance_map.get(gid)
            offer_active = (display_offer_active_map or {}).get(gid, False)
            original_price = (display_original_price_map or {}).get(gid) if offer_active else None
            # Gym not in geo cache (e.g. unverified, no location row): send 0.0
            # so the frontend renderer doesn't choke on null. Only when a geo
            # lookup actually ran (distance_map non-empty); otherwise stay null.
            if distance is not None:
                distance_km_value = round(distance, 2)
            elif distance_map:
                distance_km_value = 0.0
            else:
                distance_km_value = None
            results.append(
                MembershipGymResponse(
                    gym_id=gid,
                    gym_name=gym.name.upper() if gym.name else None,
                    cover_pic=cover_pics.get(gid, ""),
                    area=gym.area,
                    distance_km=distance_km_value,
                    views=views_map.get(gid, 0),
                    frequently_booked=gid in freq_booked_set,
                    membership_price=price,
                    original_membership_price=original_price,
                    plan_id=display_plan_id_map.get(gid),
                    duration=display_duration_map.get(gid),
                    no_cost_emi=emi_map.get(gid, False),
                    offer_active=offer_active,
                )
            )
        return results

    @staticmethod
    def _select_display_plan(
        plans: List[GymPlans],
        membership_types: Optional[List[str]] = None,
        gym_offers: Optional[Dict] = None,
    ) -> Optional[Tuple[int, int, int, bool, Optional[int]]]:
        """Return (price, plan_id, duration, offer_active, original_price) or None."""
        if not plans:
            return None

        if membership_types:
            filtered = [p for p in plans if _plan_matches_membership_types(p, membership_types)]
            if not filtered:
                return None
            filtered.sort(key=lambda p: (p.duration, p.amount))
            chosen = filtered[0]
        else:
            sorted_plans = sorted(plans, key=_plan_sort_key)
            chosen = sorted_plans[0]

        multiplier = get_markup_multiplier()
        base, offer_active = _resolve_base_amount(chosen, gym_offers or {})
        price = smart_round_price(base * multiplier)
        original_price = smart_round_price(chosen.amount * multiplier) if offer_active else None

        daily_offer_discount = get_daily_offer_discount()
        if daily_offer_discount > 0:
            price = max(price - daily_offer_discount, 0)

        return price, chosen.id, chosen.duration, offer_active, original_price

    def _empty_response(
        self, params: MembershipListParams, total_count: int = 0
    ) -> MembershipListResponse:
        total_pages = max(1, (total_count + params.limit - 1) // params.limit) if total_count else 0
        return MembershipListResponse(
            data=[],
            pagination=self._build_pagination_meta(
                params.page, params.limit, total_count, total_pages
            ),
        )

    # =====================================================================
    #  /gym_details  -- full gym page with all plans
    # =====================================================================

    async def get_gym_details(self, gym_id: int, client_id: int = None) -> Optional[GymDetailsResponse]:

        # Bypass fittbot_verified gate for whitelisted client_id (matches /gyms behavior).
        show_all_gyms = False
        unique_env = os.getenv("UNIQUE_CLIENT_ID")
        if unique_env and client_id is not None:
            allowed = {s.strip() for s in unique_env.split(",") if s.strip()}
            if str(client_id) in allowed:
                show_all_gyms = True

        # Check if walkaway discount is active for this user
        walkaway_active = False
        walkaway_show_modal = False
        if client_id:
            walkaway_key = get_walkaway_redis_key(client_id)
            walkaway_active = bool(await self.redis.exists(walkaway_key))

            if not walkaway_active:
                visited_key = get_walkaway_visited_key(client_id)
                if bool(await self.redis.exists(visited_key)):
                    ttl = get_seconds_until_midnight_ist()
                    await self.redis.set(walkaway_key, "1", ex=ttl)
                    walkaway_active = True

            if walkaway_active:
                modal_key = f"{walkaway_key}:modal_shown"
                ttl = get_seconds_until_midnight_ist()
                claimed = await self.redis.set(modal_key, "1", nx=True, ex=ttl)
                walkaway_show_modal = bool(claimed)

        gym_where = [Gym.gym_id == gym_id]
        if not show_all_gyms:
            gym_where.append(Gym.fittbot_verified.is_(True))
        gym_result = await self.db.execute(
            select(Gym).where(*gym_where).limit(1)
        )
        gym = gym_result.scalars().first()
        if not gym:
            return None

        photos_result = await self.db.execute(
            select(GymStudiosPic).where(GymStudiosPic.gym_id == gym_id)
        )
        plans_result = await self.db.execute(
            select(GymPlans).where(GymPlans.gym_id == gym_id)
        )
        emi_result = await self.db.execute(
            select(NoCostEmi).where(NoCostEmi.gym_id == gym_id).limit(1)
        )
        location_result = await self.db.execute(
            select(GymLocation).where(GymLocation.gym_id == gym_id).limit(1)
        )
        cover_pic_result = await self.db.execute(
            select(GymStudiosPic).where(
                GymStudiosPic.gym_id == gym_id,
                GymStudiosPic.type == "cover_pic",
            ).limit(1)
        )

        photos = photos_result.scalars().all()
        gym_plans = plans_result.scalars().all()
        emi_record = emi_result.scalars().first()
        location = location_result.scalars().first()
        cover_pic_record = cover_pic_result.scalars().first()

        # Fetch active offers for this gym
        offers_by_gym = await self.mem_repo.fetch_active_offers([gym_id])
        gym_offers = offers_by_gym.get(gym_id, {})

        gym_no_cost_emi = bool(emi_record and emi_record.no_cost_emi)
        cover_pic_url = cover_pic_record.image_url if cover_pic_record else gym.cover_pic
        multiplier = get_markup_multiplier()

        # Check daily offer (even-date discount)
        daily_offer_discount = get_daily_offer_discount()
        daily_offer_active = daily_offer_discount > 0

        # Build duration_count for duplicate flag
        duration_count: Dict[tuple, int] = {}
        for plan in gym_plans:
            key = (plan.duration, plan.personal_training, plan.plan_for)
            duration_count[key] = duration_count.get(key, 0) + 1

        # Build plans list — uniform smart_round_price for all plans
        plans: List[PlanItem] = []
        for plan in gym_plans:
            # Resolve base amount: use offer_price if active, else plan.amount
            base_amount, plan_offer_active = _resolve_base_amount(plan, gym_offers)
            increased_amount = smart_round_price(base_amount * multiplier)

            # Original price before offer (for strikethrough display)
            original_amount_before_offer = (
                smart_round_price(plan.amount * multiplier) if plan_offer_active else None
            )

            increased_original = smart_round_price(plan.original_amount * multiplier) if plan.original_amount else None
            per_month = round(increased_amount / plan.duration) if plan.duration > 0 else increased_amount

            nutritional_plan = _calculate_nutritional_plan(plan.duration)
            fittbot_offer = _calculate_fittbot_plan_offer(plan.duration)

            nutrition_saving = (nutritional_plan["consultations"] * 1000) if nutritional_plan else 0
            fymble_saving = BASE_FITTBOT_MONTHLY * plan.duration
            user_saving_price = nutrition_saving + fymble_saving

            # Apply daily offer discount directly to amount
            if daily_offer_active:
                increased_amount = max(increased_amount - daily_offer_discount, 0)

            # Apply walkaway 5% discount
            if walkaway_active:
                increased_amount = apply_walkaway_discount(increased_amount)

            plan_no_cost_emi = gym_no_cost_emi and increased_amount >= 4000
            dup_key = (plan.duration, plan.personal_training, plan.plan_for)

            plans.append(PlanItem(
                plan_id=plan.id,
                plan_name=plan.plans,
                amount=increased_amount,
                original_amount_before_offer=original_amount_before_offer,
                duration=plan.duration,
                description=plan.description,
                services=plan.services,
                personal_training=plan.personal_training or False,
                original=increased_original,
                offer_active=plan_offer_active,
                bonus=plan.bonus,
                bonus_type=plan.bonus_type,
                pause=plan.pause,
                pause_type=plan.pause_type,
                fittbot_plan_offer=fittbot_offer,
                is_couple=plan.plan_for == "couple",
                plan_for=plan.plan_for,
                buddy_count=plan.buddy_count,
                nutritional_plan=nutritional_plan,
                no_cost_emi=plan_no_cost_emi,
                per_month=per_month,
                user_saving_price=user_saving_price,
                duplicate=duration_count[dup_key] > 1,
                sessions_count=plan.sessions_count,
                discount=(increased_original - increased_amount) if increased_original and increased_original > increased_amount else None,
            ))

        # Sort plans by duration then amount
        plans.sort(key=lambda p: (p.duration, p.amount))

        # Cache user_saving_price per gym
        savings_map = {str(p.plan_id): p.user_saving_price for p in plans}
        asyncio.create_task(
            self.redis.set(f"gym:{gym_id}:user_savings", json.dumps(savings_map), ex=86400)
        )

        # Location
        exact_location = None
        if location:
            exact_location = {
                "latitude": location.latitude,
                "longitude": location.longitude,
            }


        return GymDetailsResponse(
            data=GymDetailsData(
                gym_id=gym.gym_id,
                gym_name=gym.name.upper() if gym.name else None,
                cover_pic=cover_pic_url,
                address=GymAddress(
                    door_no=gym.door_no,
                    building=gym.building,
                    street=gym.street,
                    area=gym.area,
                    city=gym.city,
                    state=gym.state,
                    pincode=gym.pincode,
                ),
                contact_number=gym.contact_number,
                services=_safe_json_struct(gym.services),
                operating_hours=_safe_json_struct(gym.operating_hours),
                gym_timings=_safe_json_struct(gym.gym_timings),
                photos=[
                    GymPhotoItem(photo_id=p.photo_id, type=p.type, image_url=p.image_url)
                    for p in photos
                ],
                plans=plans,
                no_cost_emi=gym_no_cost_emi,
                exact_location=exact_location,
                daily_offer_active=daily_offer_active,
                walkaway_discount_active=walkaway_active,
                walkaway_show_modal=walkaway_show_modal,
            ),
        )
