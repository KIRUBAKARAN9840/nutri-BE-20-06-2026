import uuid
from datetime import date, datetime, timedelta, time
from typing import Dict, List, Set
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from redis import asyncio as aioredis

from app.models.nutrition_models import NutritionSchedule, NutritionBooking
from app.fittbot_api.v1.payments.models.credits import CreditBalance, CreditLedger

NUTRITION_PRICE_MINOR = 149900  # ₹1499
NUTRITIONIST_ID = 1
SCHEDULE_WINDOW_DAYS = 30
NEW_USER_BONUS_CREDITS = 3


def _time_to_12h(t: time) -> str:
    """Convert time object to '02:30 PM' format."""
    return datetime.combine(date.min, t).strftime("%I:%M %p")


class NutritionPurchaseService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis

    async def get_preview(self, client_id: int) -> dict:
        """Return pricing info for the nutrition purchase checkout preview."""
        return {
            "status": 200,
            "price": 1499,
            "price_minor": NUTRITION_PRICE_MINOR,
        }

    # ── GET /status — credit balance + nutrition purchased ──────────

    async def get_nutrition_status(self, client_id: int) -> dict:
        """Return credit balance and whether nutrition has been purchased."""
        # Sweep any expired signup/subscription grants before reading balance
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service_async import (
            expire_stale_credits_isolated,
        )
        await expire_stale_credits_isolated(client_id, redis=self.redis)

        # Credit balance — grant 3 free credits once if no row exists yet.
        bal_result = await self.db.execute(
            select(CreditBalance).where(CreditBalance.client_id == client_id)
        )
        balance_row = bal_result.scalars().first()

        is_unlimited = False
        if balance_row is None:
            credits = await self._grant_new_user_bonus(client_id)
        else:
            credits = balance_row.balance or 0
            # Active unlimited-scan pass (credit_999)? Compare tz-naive to avoid
            # naive/aware mismatch on the DB-returned datetime.
            uu = balance_row.unlimited_until
            if uu is not None:
                now_naive = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
                uu_naive = uu.replace(tzinfo=None) if uu.tzinfo else uu
                is_unlimited = uu_naive > now_naive

        # Nutrition purchased — any active booking within 31 days
        today = datetime.now().date()
        cutoff = today - timedelta(days=31)

        stmt = (
            select(NutritionBooking.id)
            .where(
                NutritionBooking.client_id == client_id,
                NutritionBooking.booking_date >= cutoff,
                NutritionBooking.status.in_(["booked", "attended"]),
            )
            .limit(1)
        )
        result = await self.db.execute(stmt)
        nutrition_purchased = result.scalar() is not None

        return {
            "credits": credits,
            "nutrition_purchased": nutrition_purchased,
            "is_unlimited": is_unlimited,
        }

    async def _grant_new_user_bonus(self, client_id: int) -> int:

        dedup_key = f"new_user_bonus_{client_id}"
        ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))

        try:
            self.db.add(CreditBalance(
                client_id=client_id,
                balance=NEW_USER_BONUS_CREDITS,
                total_purchased=0,
                total_bonus=NEW_USER_BONUS_CREDITS,
                total_used=0,
            ))
            self.db.add(CreditLedger(
                id=f"crl_{int(ist_now.timestamp())}_{str(uuid.uuid4())[:8]}",
                client_id=client_id,
                txn_type="new_user_bonus",
                credits=NEW_USER_BONUS_CREDITS,
                balance_after=NEW_USER_BONUS_CREDITS,
                source_order_id=dedup_key,
                description=f"New user bonus ({NEW_USER_BONUS_CREDITS} free credits)",
                expires_at=None,
                created_at=ist_now,
            ))
            await self.db.commit()
            return NEW_USER_BONUS_CREDITS
        except IntegrityError:
            await self.db.rollback()
            bal_result = await self.db.execute(
                select(CreditBalance.balance).where(CreditBalance.client_id == client_id)
            )
            return bal_result.scalar() or 0

    # ── Shared: fetch active schedules (single query, reused) ─────────

    async def _get_active_schedules(self) -> List[NutritionSchedule]:
        return (
            await self.db.execute(
                select(NutritionSchedule).where(
                    NutritionSchedule.nutritionist_id == NUTRITIONIST_ID,
                    NutritionSchedule.is_active.is_(True),
                )
            )
        ).scalars().all()

    # ── GET /dates ────────────────────────────────────────────────────

    async def get_available_dates(self) -> dict:
        """Return sorted available dates for the next 30 days."""
        today = date.today()
        end_range = today + timedelta(days=SCHEDULE_WINDOW_DAYS)
        schedules = await self._get_active_schedules()

        # Build weekday → schedules index for O(1) lookup
        weekday_map: Dict[int, List[NutritionSchedule]] = {}
        for sch in schedules:
            weekday_map.setdefault(sch.weekday, []).append(sch)

        # Jump by weekday instead of iterating every day
        available_dates: List[date] = []
        for weekday, scheds in weekday_map.items():
            # First occurrence of this weekday from today
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

        return {
            "status": 200,
            "data": [d.isoformat() for d in available_dates],
        }

    # ── GET /slots ────────────────────────────────────────────────────

    async def get_slots_for_date(self, selected_date: date) -> dict:
        """Return available slots for a given date."""
        today = date.today()
        end_range = today + timedelta(days=SCHEDULE_WINDOW_DAYS)
        schedules = await self._get_active_schedules()

        # Cannot book for today
        if selected_date == today:
            return {"status": 200, "data": []}

        # Fetch booked schedule_ids for this date in one query
        booking_rows = (
            await self.db.execute(
                select(NutritionBooking.schedule_id, NutritionBooking.booking_date).where(
                    NutritionBooking.nutritionist_id == NUTRITIONIST_ID,
                    NutritionBooking.booking_date == selected_date,
                    NutritionBooking.status.in_(["booked", "pending", "attended"]),
                )
            )
        ).all()
        booked_slots: Set[tuple] = {(row.schedule_id, row.booking_date) for row in booking_rows}

        # Filter schedules valid for this date
        day_slots = []
        for sch in schedules:
            if sch.start_date and selected_date < sch.start_date:
                continue
            if sch.end_date and selected_date > sch.end_date:
                continue
            if selected_date.weekday() != sch.weekday:
                continue

            is_booked = (sch.id, selected_date) in booked_slots

            day_slots.append({
                "schedule_id": sch.id,
                "start_time": _time_to_12h(sch.start_time),
                "end_time": _time_to_12h(sch.end_time),
                "is_booked": is_booked,
            })

        # Sort by start_time
        day_slots.sort(key=lambda x: datetime.strptime(x["start_time"], "%I:%M %p"))

        return {"status": 200, "data": day_slots}
