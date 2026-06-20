"""Business logic for Nutrition Diet templates.

Fetches the latest assigned nutrition diet template for a client,
resolves the template name and full diet_data from the diet_templates table.
"""

from datetime import date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.logging_utils import FittbotHTTPException
from ..utils import normalize_meal_title
from .repository import NutritionDietRepository
from .schemas import (
    AddStepRequest,
    GetNutritionDietResponse,
    MessageResponse,
    NutritionDietData,
)


class NutritionDietService:

    def __init__(self, db: AsyncSession):
        self.repo = NutritionDietRepository(db)

    async def get_nutrition_diet(self, client_id: int) -> GetNutritionDietResponse:
        client_template = await self.repo.get_latest_client_template(client_id)
        if not client_template:
            return GetNutritionDietResponse(
                data=None,
                message="No nutrition diet assigned",
            )

        diet_template = await self.repo.get_diet_template(client_template.template_id)
        if not diet_template:
            raise FittbotHTTPException(
                status_code=404,
                detail="Assigned diet template not found",
                error_code="NUTRITION_DIET_TEMPLATE_NOT_FOUND",
                log_data={
                    "client_id": client_id,
                    "template_id": client_template.template_id,
                },
            )

        nutritionist_name = await self.repo.get_nutritionist_name(
            client_template.nutritionist_id
        ) or "Unknown"

        # Stamp is_logged on each meal so the frontend can disable re-adding.
        diet_data = diet_template.diet_data or []
        instruction= diet_template.description or ""
        logged_keys = await self.repo.get_logged_meal_keys(client_template.id)
        self._mark_logged_meals(diet_data, logged_keys)

        # Per-day target_calories (existing key if present, else sum of food
        # calories rounded). consumed_calories is single-valued at data level
        # — it's today's ActualDiet, the same regardless of which plan day.
        self._stamp_target_per_day(diet_data)
        # day_number → calendar date, where day 1 is the day after assigned_date.
        self._stamp_date_per_day(diet_data, client_template.assigned_date)
        consumed = await self.repo.fetch_consumed_calories_today(client_id)

        return GetNutritionDietResponse(
            data=NutritionDietData(
                id=client_template.id,
                nutritionist_name=nutritionist_name,
                step=client_template.step or 0,
                consumed_calories=consumed,
                diet_data=diet_data,
                instructions=instruction,
            ),
        )

    @staticmethod
    def _mark_logged_meals(diet_data: list, logged_keys: set) -> None:
        """Mutate diet_data in-place: set is_logged on each meal.

        Expects structure:
            [{"day_number": 1, "meals": [{"title": "...", "foods": [...]}, ...]}, ...]
        Match key is (day_number, normalized_title).
        """
        if not diet_data or not isinstance(diet_data, list):
            return

        for day in diet_data:
            if not isinstance(day, dict):
                continue
            day_number = day.get("day_number")
            meals = day.get("meals") or []
            if day_number is None or not isinstance(meals, list):
                continue
            for meal in meals:
                if not isinstance(meal, dict):
                    continue
                title_norm = normalize_meal_title(meal.get("title", ""))
                meal["is_logged"] = (day_number, title_norm) in logged_keys

    @staticmethod
    def _stamp_target_per_day(diet_data: list) -> None:
        """Mutate diet_data: set target_calories on every day block.

        Uses the existing target_calories key if present (cast to int); else
        sums nutrition.calories across all foods in the day, rounded.
        """
        if not isinstance(diet_data, list):
            return
        for day in diet_data:
            if not isinstance(day, dict):
                continue
            existing = day.get("target_calories")
            if existing is not None:
                try:
                    day["target_calories"] = int(round(float(existing)))
                except (TypeError, ValueError):
                    day["target_calories"] = 0
                continue
            total = 0.0
            for meal in day.get("meals") or []:
                if not isinstance(meal, dict):
                    continue
                for food in meal.get("foods") or []:
                    if not isinstance(food, dict):
                        continue
                    cal = (food.get("nutrition") or {}).get("calories", 0) or 0
                    try:
                        total += float(cal)
                    except (TypeError, ValueError):
                        continue
            day["target_calories"] = int(round(total))

    @staticmethod
    def _stamp_date_per_day(diet_data: list, assigned_date: date) -> None:
        """Mutate diet_data: set ISO date on every day block.

        day 1 starts the day *after* assigned_date, day 2 the day after that,
        etc. — so date = assigned_date + day_number days.
        """
        if not isinstance(diet_data, list) or assigned_date is None:
            return
        for day in diet_data:
            if not isinstance(day, dict):
                continue
            day_number = day.get("day_number")
            if not isinstance(day_number, int) or day_number < 1:
                continue
            day["date"] = (assigned_date + timedelta(days=day_number)).isoformat()

    async def add_step(self, client_id: int, req: AddStepRequest) -> MessageResponse:
        updated = await self.repo.update_step(client_id, req.id, req.step)
        if not updated:
            raise FittbotHTTPException(
                status_code=404,
                detail="No nutrition diet assigned",
                error_code="NUTRITION_DIET_NOT_FOUND",
                log_data={"client_id": client_id},
            )
        return MessageResponse(message="Step updated successfully")

