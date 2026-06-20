"""Business logic for Log Food.

Handles saving/merging template diet data into ActualDiet.
"""

import time
import random
from datetime import datetime
from typing import Optional

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog
from ..utils import normalize_meal_title
from .repository import LogFoodRepository
from .schemas import (
    LogFoodRequest,
    LogFoodResponse,
    LogScannedFoodRequest,
    LogScannedFoodResponse,
)

DEFAULT_TEMPLATE = [
    {"id": "1", "title": "Breakfast", "tagline": "Start your day right", "foodList": [], "timeRange": "8:30-9:30 AM", "itemsCount": 0},
    {"id": "2", "title": "Lunch", "tagline": "Nutritious midday meal", "foodList": [], "timeRange": "1:00-2:00 PM", "itemsCount": 0},
    {"id": "3", "title": "Snacks", "tagline": "Healthy bites", "foodList": [], "timeRange": "4:00-5:00 PM", "itemsCount": 0},
    {"id": "4", "title": "Dinner", "tagline": "End your day well", "foodList": [], "timeRange": "7:30-8:30 PM", "itemsCount": 0},
]


class LogFoodService:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = LogFoodRepository(db)
        self.redis = redis

    async def add_food(self, client_id: int, request: LogFoodRequest) -> LogFoodResponse:
        record = await self.repo.get_actual_diet(client_id, request.date)

        if not record or record.diet_data is None:
            full = self._expand_to_full_template(request.diet_data)
            if record:
                await self.repo.update_diet_data(record, full)
            else:
                await self.repo.create_actual_diet(client_id, request.date, full)
        else:
            merged = self._merge_templates(record.diet_data, request.diet_data)
            await self.repo.update_diet_data(record, merged)

        # Mark the (template, day, title) entries as logged so the
        # nutrition_diet view can disable re-adding. Only runs when both
        # template context fields are provided by the frontend.
        await self._record_template_meal_logs(client_id, request)

        await self._invalidate_macros_cache(client_id)

        # XP calculation (5 XP per log, max 50/day)
        calorie_points = await self._award_xp(client_id, request.date)

        # Check feedback status
        feedback = await self._check_feedback(client_id)

        # Check target exceeded
        target = await self._check_target_exceeded(client_id, request.date)

        return LogFoodResponse(
            message="Diet logged successfully",
            reward_point=calorie_points,
            xp_earned=calorie_points,
            feedback=feedback,
            target=target,
        )

    async def _invalidate_macros_cache(self, client_id: int) -> None:
        from datetime import date
        try:
            await self.redis.delete(f"{client_id}:target_actual:{date.today().isoformat()}")
        except RedisError as e:
            jlog("warning", {
                "type": "cache_invalidate_failure",
                "error_code": "LOG_FOOD_MACROS_CACHE_INVALIDATE",
                "detail": str(e),
                "client_id": client_id,
            })

    async def _record_template_meal_logs(
        self, client_id: int, request: LogFoodRequest
    ) -> None:
        """Persist (client_template_id, day_number, title) markers for each
        meal slot in the request that has at least one food item.

        No-op when the request didn't come from a nutritionist's plan
        (client_template_id / day_number absent). Wrapped in try/except so
        a failure here can never break the actual food log.
        """
        if request.client_template_id is None or request.day_number is None:
            return

        rows = []
        seen_norms: set = set()
        for meal in (request.diet_data or []):
            if not isinstance(meal, dict):
                continue
            foods = meal.get("foodList") or meal.get("foods") or []
            if not foods:
                continue
            title_raw = meal.get("title") or ""
            title_norm = normalize_meal_title(title_raw)
            if not title_norm or title_norm in seen_norms:
                continue
            seen_norms.add(title_norm)
            rows.append({"title": title_raw, "title_norm": title_norm})

        if not rows:
            return

        try:
            await self.repo.insert_template_meal_logs(
                client_id=client_id,
                client_template_id=request.client_template_id,
                day_number=request.day_number,
                rows=rows,
            )
        except Exception as e:
            jlog("warning", {
                "type": "nutrition_diet_meal_log_failure",
                "error_code": "LOG_FOOD_TEMPLATE_LOG_INSERT",
                "detail": str(e),
                "client_id": client_id,
                "client_template_id": request.client_template_id,
                "day_number": request.day_number,
            })

    # ── Scanner → Diet ─────────────────────────────────────────────

    async def add_scanned_food(
        self, client_id: int, req: LogScannedFoodRequest
    ) -> LogScannedFoodResponse:
        """Save food-scanner results into ActualDiet (same merge as /add)."""
        today = datetime.strptime(req.date.split("T")[0], "%Y-%m-%d").date()
        sd = req.scanner_data.model_dump()

        # Build food item from scanner data
        primary_food = sd.get("primary_food", "")
        items_list = sd.get("items", [])

        if primary_food:
            food_name = primary_food
        elif items_list and isinstance(items_list[0], dict):
            food_name = items_list[0].get("name", "Scanned Food")
        elif items_list and isinstance(items_list[0], str):
            food_name = "+".join(items_list)
        else:
            food_name = "Scanned Food"

        food_item = {
            "id": f"{int(time.time() * 1000000)}{random.randint(10000, 99999)}",
            "name": food_name,
            "calories": sd.get("totals", {}).get("calories", 0),
            "protein": sd.get("totals", {}).get("protein_g", 0),
            "carbs": sd.get("totals", {}).get("carbs_g", 0),
            "fat": sd.get("totals", {}).get("fat_g", 0),
            "fiber": sd.get("totals", {}).get("fibre_g", 0),
            "sugar": sd.get("totals", {}).get("sugar_g", 0),
            "sodium": sd.get("micro_nutrients", {}).get("sodium_mg", 0),
            "calcium": sd.get("micro_nutrients", {}).get("calcium_mg", 0),
            "magnesium": sd.get("micro_nutrients", {}).get("magnesium_mg", 0),
            "potassium": sd.get("micro_nutrients", {}).get("potassium_mg", 0),
            "iron": sd.get("micro_nutrients", {}).get("iron_mg", 0),
            "quantity": "1 serving",
            "image_url": "",
        }

        # Reuse /add merge logic — wrap food_item in a single-meal template
        incoming = [{
            "title": req.meal_category,
            "foodList": [food_item],
        }]

        record = await self.repo.get_actual_diet(client_id, today)
        if not record or record.diet_data is None:
            full = self._expand_to_full_template(incoming)
            if record:
                await self.repo.update_diet_data(record, full)
            else:
                await self.repo.create_actual_diet(client_id, today, full)
        else:
            merged = self._merge_templates(record.diet_data, incoming)
            await self.repo.update_diet_data(record, merged)

        await self._invalidate_macros_cache(client_id)

        # XP calculation (5 XP per scan, max 50/day)
        calorie_points = await self._award_xp(client_id, today)

        # Check feedback status
        feedback = await self._check_feedback(client_id)

        # Check target exceeded
        target = await self._check_target_exceeded(client_id, today)

        return LogScannedFoodResponse(
            reward_point=calorie_points,
            xp_earned=calorie_points,
            feedback=feedback,
            target=target,
        )

    async def _award_xp(self, client_id: int, today) -> int:
        """Award 5 XP per scan, capped at 50 XP/day."""
        from app.models.fittbot_models import CalorieEvent

        XP_PER_SCAN = 5
        MAX_DAILY_DIET_XP = 50

        # Get or create calorie event
        result = await self.repo.db.execute(
            select(CalorieEvent).where(
                CalorieEvent.client_id == client_id,
                CalorieEvent.event_date == today,
            )
        )
        calorie_event = result.scalars().first()

        if not calorie_event:
            calorie_event = CalorieEvent(client_id=client_id, event_date=today, calories_added=0)
            self.repo.db.add(calorie_event)
            await self.repo.db.flush()

        added = calorie_event.calories_added or 0
        if added >= MAX_DAILY_DIET_XP:
            return 0

        points_to_award = min(XP_PER_SCAN, MAX_DAILY_DIET_XP - added)
        calorie_event.calories_added = added + points_to_award

        await self.repo.db.commit()
        return points_to_award

    async def _check_feedback(self, client_id: int) -> bool:
        try:
            from app.fittbot_api.v1.client.client_api.side_bar.ratings import check_feedback_status
            return check_feedback_status(self.repo.db, client_id)
        except Exception:
            return False

    async def _check_target_exceeded(self, client_id: int, today) -> bool:
        from app.models.fittbot_models import ClientTarget, ActualDiet

        result = await self.repo.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        client_target = result.scalars().first()
        if not client_target or not client_target.calories:
            return False

        result = await self.repo.db.execute(
            select(ActualDiet).where(
                ActualDiet.client_id == client_id,
                ActualDiet.date == today,
            )
        )
        actual = result.scalars().first()
        if not actual or not actual.diet_data:
            return False

        # Calculate total calories from all meals
        total_cal = 0
        for meal in actual.diet_data:
            for food in meal.get("foodList", []):
                total_cal += food.get("calories", 0)

        if total_cal <= client_target.calories:
            return False

        # Show once per day via Redis
        redis_key = f"diet_target_achieved:{client_id}:{client_target.calories}:{today}"
        try:
            if await self.redis.exists(redis_key):
                return False
            await self.redis.set(redis_key, "1", ex=86400)
            return True
        except RedisError:
            return False

    # ── Template logic ────────────────────────────────────────────

    @staticmethod
    def _find_meal(meals: list, target: dict) -> Optional[dict]:
        """Locate a meal slot.

        Match priority:
          1. Normalized title (canonical identity — "Breakfast" == "breakfast").
          2. Id, only as a fallback when there's no title to match on.

        Title-first because the frontend's id is its own local index and can
        drift from the persisted backend id once custom meals are added.
        Returns None if nothing matches — caller appends as a new meal.
        """
        t_id = str(target.get("id") or "")
        t_title_norm = normalize_meal_title(target.get("title", ""))

        # Pass 1: normalized title match (preferred)
        if t_title_norm:
            for m in meals:
                if normalize_meal_title(m.get("title", "")) == t_title_norm:
                    return m

        # Pass 2: id fallback (only if no title was provided to match on)
        if not t_title_norm and t_id:
            for m in meals:
                if str(m.get("id") or "") == t_id:
                    return m

        return None

    @staticmethod
    def _expand_to_full_template(incoming: list) -> list:
        """Start with 4-meal scaffold, overlay incoming meals into matching slots."""
        full = [m.copy() for m in DEFAULT_TEMPLATE]
        for inc_meal in incoming:
            dest = LogFoodService._find_meal(full, inc_meal)
            if dest:
                dest["foodList"] = inc_meal.get("foodList", [])
                dest["itemsCount"] = len(dest["foodList"])
            else:
                full.append({
                    "id": str(len(full) + 1),
                    "title": inc_meal.get("title", "Custom"),
                    "tagline": inc_meal.get("tagline", ""),
                    "foodList": inc_meal.get("foodList", []),
                    "timeRange": inc_meal.get("timeRange", ""),
                    "itemsCount": len(inc_meal.get("foodList", [])),
                })
        return full

    @staticmethod
    def _merge_templates(existing: list, incoming: list) -> list:
        """Append incoming foods into matching meal slots."""
        for in_meal in incoming:
            new_foods = in_meal.get("foodList", []) or []
            if not new_foods:
                continue

            ex_meal = LogFoodService._find_meal(existing, in_meal)
            if not ex_meal:
                existing.append({
                    "id": str(len(existing) + 1),
                    "title": in_meal.get("title", "Custom"),
                    "tagline": in_meal.get("tagline", ""),
                    "foodList": new_foods,
                    "timeRange": in_meal.get("timeRange", ""),
                    "itemsCount": len(new_foods),
                })
                continue

            ex_foods = ex_meal.get("foodList", []) or []
            for food in new_foods:
                copy = food.copy()
                copy["id"] = str(int(time.time() * 1000)) + str(random.randint(10000, 99999))
                ex_foods.append(copy)

            ex_meal["foodList"] = ex_foods
            ex_meal["itemsCount"] = len(ex_foods)

        return existing


