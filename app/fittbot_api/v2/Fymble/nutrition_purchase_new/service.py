
import logging
from datetime import date, datetime, timedelta, time
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from redis import asyncio as aioredis

from app.models.nutrition_models import (
    NutritionBooking,
    NutritionEligibility,
    NutritionSchedule,
)
from app.fittbot_api.v1.payments.models.credits import CreditBalance

from app.models.nutrition_models import Nutritionist
from app.fittbot_api.v2.Fymble_Payments.nutrition_purchase_new import slot_hold

from .schemas import SESSION_SCHEDULE, NUTRITION_PRICE, NUTRITION_PRICE_MINOR

logger = logging.getLogger("nutrition_purchase_new.slots")



SCHEDULE_WINDOW_DAYS = 30


def _time_to_12h(t: time) -> str:
    return datetime.combine(date.min, t).strftime("%I:%M %p")


def _parse_time(s: str) -> time:
    """Parse HH:MM or HH:MM:SS string to time object."""
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)


def _add_minutes(t: time, minutes: int) -> time:
    """Add minutes to a time object."""
    dt = datetime.combine(date.min, t) + timedelta(minutes=minutes)
    return dt.time()


def _times_overlap(s1: time, e1: time, s2: time, e2: time) -> bool:
    """Check if two time ranges overlap."""
    return s1 < e2 and s2 < e1


class NutritionPurchaseNewService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis

    # ── GET /data — package preview ──────────────────────────────────

    async def get_preview(self) -> dict:
        return {
            "status": 200,
            "price": NUTRITION_PRICE,
            "price_minor": NUTRITION_PRICE_MINOR,
            "total_sessions": 4,
            "session_schedule": SESSION_SCHEDULE,
        }

    # ── GET /status — package status for this client ─────────────────

    async def get_package_status(self, client_id: int) -> dict:
        # Sweep any expired signup/subscription grants before reading balance
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service_async import (
            expire_stale_credits_isolated,
        )
        await expire_stale_credits_isolated(client_id, redis=self.redis)

        # Credit balance
        bal_result = await self.db.execute(
            select(CreditBalance.balance).where(CreditBalance.client_id == client_id)
        )
        credits = bal_result.scalar() or 0

        # Find active eligibility with remaining sessions
        eligibility = await self._get_active_eligibility(client_id)

        if not eligibility:
            return {
                "status": 200,
                "has_active_package": False,
                "credits": credits,
                "total_sessions": 0,
                "sessions_used": 0,
                "sessions_remaining": 0,
            }

        next_seq = eligibility.used_sessions + 1
        schedule = eligibility.session_schedule or SESSION_SCHEDULE

        # Find the next session config
        next_session = None
        for s in schedule:
            if s["seq"] == next_seq:
                next_session = s
                break

        if not next_session:
            return {
                "status": 200,
                "has_active_package": True,
                "credits": credits,
                "total_sessions": eligibility.total_sessions,
                "sessions_used": eligibility.used_sessions,
                "sessions_remaining": eligibility.remaining_sessions,
                "eligibility_id": eligibility.id,
            }

        # Calculate unlock date
        unlocked = True
        unlock_date = None
        if next_session["unlock_after_days"] > 0 and eligibility.last_booking_date:
            unlock_date_val = eligibility.last_booking_date + timedelta(
                days=next_session["unlock_after_days"]
            )
            unlocked = date.today() >= unlock_date_val
            if not unlocked:
                unlock_date = unlock_date_val.isoformat()

        return {
            "status": 200,
            "has_active_package": True,
            "credits": credits,
            "total_sessions": eligibility.total_sessions,
            "sessions_used": eligibility.used_sessions,
            "sessions_remaining": eligibility.remaining_sessions,
            "next_session_number": next_seq,
            "next_session_duration": next_session["duration_minutes"],
            "next_session_unlocked": unlocked,
            "next_unlock_date": unlock_date,
            "eligibility_id": eligibility.id,
        }

    # ── GET /dates — available booking dates ─────────────────────────

    async def get_available_dates(self) -> dict:
        today = date.today()
        end_range = today + timedelta(days=SCHEDULE_WINDOW_DAYS)
        schedules = await self._get_active_schedules()

        weekday_map: Dict[int, List[NutritionSchedule]] = {}
        for sch in schedules:
            weekday_map.setdefault(sch.weekday, []).append(sch)

        available_dates: List[date] = []
        for weekday, scheds in weekday_map.items():
            days_ahead = (weekday - today.weekday()) % 7
            first = today + timedelta(days=days_ahead)

            d = first
            while d <= end_range:
                for sch in scheds:
                    start_bound = sch.start_date or today
                    end_bound = sch.end_date or end_range
                    if max(today, start_bound) <= d <= min(end_range, end_bound):
                        available_dates.append(d)
                        break
                d += timedelta(days=7)

        available_dates.sort()
        return {"status": 200, "data": [d.isoformat() for d in available_dates]}

    # ── GET /slots — user-aware slot generation ──────────────────────

    async def get_slots_for_date(
        self, client_id: int, selected_date: date
    ) -> dict:
        today = date.today()
        if selected_date <= today:
            logger.info(
                "SLOTS_DBG client=%s date=%s reason=date_in_past_or_today returned=0",
                client_id, selected_date.isoformat(),
            )
            return {"status": 200, "data": []}

        # 1. Determine user's next session duration
        eligibility = await self._get_active_eligibility(client_id)
        if not eligibility:
            # No package — show 1-hour slots (preview mode, all marked unavailable)
            next_seq = None
            schedule = None
            next_session = None
            next_duration = 60
            logger.info(
                "SLOTS_DBG client=%s date=%s eligibility=NONE fallback_duration=60",
                client_id, selected_date.isoformat(),
            )
        else:
            next_seq = eligibility.used_sessions + 1
            schedule = eligibility.session_schedule or SESSION_SCHEDULE
            schedule_source = (
                "eligibility_row" if eligibility.session_schedule else "FALLBACK_SESSION_SCHEDULE"
            )
            next_session = next((s for s in schedule if s["seq"] == next_seq), None)
            next_duration = next_session["duration_minutes"] if next_session else 60
            logger.info(
                "SLOTS_DBG client=%s date=%s elig_id=%s plan_name=%r "
                "total=%d used=%d remaining=%d last_booking=%s "
                "next_seq=%d schedule_source=%s schedule_len=%d "
                "matched_seq_entry=%s next_duration=%d",
                client_id, selected_date.isoformat(),
                eligibility.id, eligibility.plan_name,
                eligibility.total_sessions, eligibility.used_sessions,
                eligibility.remaining_sessions, eligibility.last_booking_date,
                next_seq, schedule_source, len(schedule),
                next_session, next_duration,
            )

        schedules = await self._get_active_schedules()
        day_schedules = [
            sch for sch in schedules
            if sch.weekday == selected_date.weekday()
            and (not sch.start_date or selected_date >= sch.start_date)
            and (not sch.end_date or selected_date <= sch.end_date)
        ]
        if not day_schedules:
            return {"status": 200, "data": []}

        # Group by (start_time, end_time) → list of schedule rows offering that hour
        windows: Dict[Tuple[time, time], List[NutritionSchedule]] = {}
        for sch in day_schedules:
            windows.setdefault((sch.start_time, sch.end_time), []).append(sch)

        # 3. Pull ALL bookings (any nutritionist) on this date
        booking_rows = (
            await self.db.execute(
                select(NutritionBooking).where(
                    NutritionBooking.booking_date == selected_date,
                    NutritionBooking.status.in_(["booked", "pending", "attended"]),
                )
            )
        ).scalars().all()

        # 4. For each hour-window, compute the set of nutritionists FREE for
        #    the WHOLE hour. Per Interpretation 1, any booking inside the hour
        #    burns that nutritionist for the whole hour (including the other half).
        #    Active Redis holds (ad-funnel checkouts in progress) also burn.
        slots = []
        for (start, end), schedule_rows in sorted(windows.items()):
            offering_ids = sorted({sch.nutritionist_id for sch in schedule_rows})
            burnt_by_booking = {
                b.nutritionist_id for b in booking_rows
                if _times_overlap(start, end, b.start_time, b.end_time)
            }
            burnt_by_hold = set()
            for nid in offering_ids:
                if nid in burnt_by_booking:
                    continue
                if await slot_hold.is_held(self.redis, selected_date, start, end, nid):
                    burnt_by_hold.add(nid)
            burnt_ids = burnt_by_booking | burnt_by_hold
            free_ids = [nid for nid in offering_ids if nid not in burnt_ids]
            is_available = bool(free_ids)

            # Pick a deterministic schedule_id for the response (informational).
            # Booking time re-resolves the actual nutritionist.
            preferred_nid = free_ids[0] if free_ids else offering_ids[0]
            preferred_sch_id = next(
                sch.id for sch in schedule_rows if sch.nutritionist_id == preferred_nid
            )

            if next_duration == 60:
                slots.append({
                    "schedule_id": preferred_sch_id,
                    "start_time": _time_to_12h(start),
                    "end_time": _time_to_12h(end),
                    "is_booked": not is_available,
                    "duration_minutes": 60,
                })
            else:
                # 30-min: split into two halves; both halves share availability
                # because the rule is hour-level (booking burns full hour).
                mid_time = _add_minutes(start, 30)
                slots.append({
                    "schedule_id": preferred_sch_id,
                    "start_time": _time_to_12h(start),
                    "end_time": _time_to_12h(mid_time),
                    "is_booked": not is_available,
                    "duration_minutes": 30,
                })
                slots.append({
                    "schedule_id": preferred_sch_id,
                    "start_time": _time_to_12h(mid_time),
                    "end_time": _time_to_12h(end),
                    "is_booked": not is_available,
                    "duration_minutes": 30,
                })

        # Sort by start_time
        slots.sort(key=lambda x: datetime.strptime(x["start_time"], "%I:%M %p"))

        slot_durations = sorted({s["duration_minutes"] for s in slots})
        logger.info(
            "SLOTS_DBG client=%s date=%s day_schedules=%d bookings_on_date=%d "
            "branch=%s slots_returned=%d unique_durations=%s first_slot=%s",
            client_id, selected_date.isoformat(),
            len(day_schedules), len(booking_rows),
            "60min" if next_duration == 60 else "30min_split",
            len(slots), slot_durations,
            slots[0] if slots else None,
        )

        return {"status": 200, "data": slots}

    # ── POST /book_slot — book a session post-purchase ───────────────

    async def book_slot(self, client_id: int, payload: dict) -> dict:
        from datetime import date as date_type

        booking_date = date_type.fromisoformat(payload["booking_date"])
        req_start = _parse_time(payload["start_time"])
        req_end = _parse_time(payload["end_time"])

        # 1. Validate eligibility
        eligibility = await self._get_active_eligibility(client_id)
        if not eligibility:
            raise ValueError("no_active_package")

        if eligibility.remaining_sessions <= 0:
            raise ValueError("no_sessions_remaining")

        next_seq = eligibility.used_sessions + 1
        schedule = eligibility.session_schedule or SESSION_SCHEDULE
        next_session = next((s for s in schedule if s["seq"] == next_seq), None)
        if not next_session:
            raise ValueError("no_more_sessions_in_schedule")

        # 2. Validate unlock
        if next_session["unlock_after_days"] > 0 and eligibility.last_booking_date:
            unlock_date = eligibility.last_booking_date + timedelta(
                days=next_session["unlock_after_days"]
            )
            if date.today() < unlock_date:
                raise ValueError(f"session_locked_until_{unlock_date.isoformat()}")

        expected_duration = next_session["duration_minutes"]

        sch_result = await self.db.execute(
            select(NutritionSchedule).where(
                NutritionSchedule.id == payload["schedule_id"],
                NutritionSchedule.is_active.is_(True),
            )
        )
        hint_schedule = sch_result.scalar_one_or_none()
        if not hint_schedule:
            raise ValueError("schedule_not_found_or_inactive")
        hour_start, hour_end = hint_schedule.start_time, hint_schedule.end_time
        hint_weekday = hint_schedule.weekday

        # 4. Validate date/weekday
        if booking_date <= date.today():
            raise ValueError("cannot_book_today_or_past")
        if booking_date.weekday() != hint_weekday:
            raise ValueError("booking_date_weekday_mismatch")

        # 5. Validate time range fits within the parent hour
        if req_start < hour_start or req_end > hour_end:
            raise ValueError("time_range_outside_schedule")

        # 6. Find ALL active schedule rows that offer this exact hour-window
        #    on this weekday — one per nutritionist (in the canonical seed).
        offering_rows = (
            await self.db.execute(
                select(NutritionSchedule).where(
                    NutritionSchedule.weekday == hint_weekday,
                    NutritionSchedule.start_time == hour_start,
                    NutritionSchedule.end_time == hour_end,
                    NutritionSchedule.is_active.is_(True),
                    or_(
                        NutritionSchedule.start_date.is_(None),
                        NutritionSchedule.start_date <= booking_date,
                    ),
                    or_(
                        NutritionSchedule.end_date.is_(None),
                        NutritionSchedule.end_date >= booking_date,
                    ),
                )
            )
        ).scalars().all()
        if not offering_rows:
            raise ValueError("no_active_schedule_for_window")

        active_nut_ids = set(await self._get_active_nutritionist_ids())
        offering_rows = [r for r in offering_rows if r.nutritionist_id in active_nut_ids]
        if not offering_rows:
            raise ValueError("no_active_nutritionist_for_window")

        # 7. Per Interpretation 1: a nutritionist with ANY booking overlapping
        #    the parent HOUR is burnt for the whole hour (even for the unbooked
        #    half). So free = nutritionists with no booking touching the hour.
        existing_bookings = (
            await self.db.execute(
                select(NutritionBooking).where(
                    NutritionBooking.booking_date == booking_date,
                    NutritionBooking.status.in_(["booked", "pending", "attended"]),
                )
            )
        ).scalars().all()
        burnt_ids = {
            b.nutritionist_id for b in existing_bookings
            if _times_overlap(hour_start, hour_end, b.start_time, b.end_time)
        }
        # Also exclude nutritionists with an active ad-funnel Redis hold.
        for r in offering_rows:
            if r.nutritionist_id in burnt_ids:
                continue
            if await slot_hold.is_held(
                self.redis, booking_date, hour_start, hour_end, r.nutritionist_id
            ):
                burnt_ids.add(r.nutritionist_id)
        free_offering_rows = [
            r for r in offering_rows if r.nutritionist_id not in burnt_ids
        ]
        if not free_offering_rows:
            raise ValueError("slot_time_conflict")

        # 8. Pick the free nutritionist with the lowest id (deterministic).
        chosen = sorted(free_offering_rows, key=lambda r: r.nutritionist_id)[0]

        # 9. Create booking using the CHOSEN nutritionist's schedule row.
        booking = NutritionBooking(
            client_id=client_id,
            eligibility_id=eligibility.id,
            nutritionist_id=chosen.nutritionist_id,
            schedule_id=chosen.id,
            booking_date=booking_date,
            start_time=req_start,
            end_time=req_end,
            status="booked",
            session_number=next_seq,
            duration_minutes=expected_duration,
        )
        self.db.add(booking)

        # 8. Update eligibility
        eligibility.used_sessions += 1
        eligibility.remaining_sessions -= 1
        eligibility.last_booking_date = booking_date
        self.db.add(eligibility)

        await self.db.commit()
        await self.db.refresh(booking)

        # 9. Invalidate home cache
        self._invalidate_home_cache(client_id)

        return {
            "status": 200,
            "message": "Slot booked successfully",
            "booking_id": booking.id,
            "session_number": next_seq,
            "duration_minutes": expected_duration,
            "sessions_remaining": eligibility.remaining_sessions,
        }

    # ── Helpers ───────────────────────────────────────────────────────

    async def _get_active_eligibility(
        self, client_id: int
    ) -> Optional[NutritionEligibility]:
        """Find the most recent active eligibility with remaining sessions."""
        result = await self.db.execute(
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
        return result.scalar_one_or_none()

    async def _get_active_nutritionist_ids(self) -> List[int]:
        """All currently-active nutritionist IDs, sorted ascending."""
        result = await self.db.execute(
            select(Nutritionist.id)
            .where(Nutritionist.is_active.is_(True))
            .order_by(Nutritionist.id.asc())
        )
        return [row[0] for row in result.all()]

    async def _get_active_schedules(self) -> List[NutritionSchedule]:
        """
        All active schedule rows across ALL active nutritionists. The slot
        generator groups them by (start, end) to compute aggregate availability.
        """
        active_nut_ids = await self._get_active_nutritionist_ids()
        if not active_nut_ids:
            return []
        return (
            await self.db.execute(
                select(NutritionSchedule).where(
                    NutritionSchedule.nutritionist_id.in_(active_nut_ids),
                    NutritionSchedule.is_active.is_(True),
                )
            )
        ).scalars().all()

    @staticmethod
    def _invalidate_home_cache(client_id: int) -> None:
        try:
            from app.utils.redis_config import get_redis_sync
            r = get_redis_sync()
            # Invalidate both v1 and v2 home cache
            keys = r.keys(f"home:data:{client_id}:*")
            ustate_keys = r.keys(f"home:v2:ustate:{client_id}")
            all_keys = (keys or []) + (ustate_keys or [])
            if all_keys:
                r.delete(*all_keys)
        except Exception:
            pass
