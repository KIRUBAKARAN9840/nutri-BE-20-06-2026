"""Business logic for Diet Report.

Builds a comprehensive diet report: summary with macro analysis,
today's meals breakdown, and last 7 days calorie history.
"""

from datetime import date, timedelta
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.nutrition_calculator import (
    calculate_bmr,
    ACTIVITY_MULTIPLIERS,
)
from app.fittbot_api.v1.client.client_api.home.calculate_macros import (
    calculate_macros,
)
from ..utils import sum_nutrients_from_meals
from .repository import DietReportRepository
from .schemas import (
    DietReportResponse,
    DietReportData,
    ReportSummary,
    MacroStatus,
    TodaysMacros,
    DayMeals,
    DailyCalories,
)

MACRO_KEYS = ["calories", "protein", "carbs", "fat"]
MACRO_MICRO_KEYS = ["calories", "protein", "carbs", "fat", "fiber", "sugar"]
MICRO_KEYS = ["calcium", "magnesium", "iron", "sodium", "potassium"]


MICRO_RDA = {
    "calcium": 1000,
    "magnesium": 400,
    "iron": 18,
    "sodium": 2300,
    "potassium": 2600,
}


class DietReportService:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = DietReportRepository(db, redis)

    async def get_report(self, client_id: int, report_date: Optional[date] = None) -> DietReportResponse:
        target_date = report_date or date.today()

        # sequential DB calls — AsyncSession doesn't support concurrent queries
        report_info = await self.repo.get_client_report_info(client_id)
        all_diets = await self.repo.get_all_actual_diets(client_id)

        profile = report_info["profile"]
        target = report_info["target"]

        # derive date-specific data from the single query result
        today = date.today()
        seven_days_ago = today - timedelta(days=6)

        today_diet = None
        day_diet = None
        last_7_diets = []

        for record in all_diets:
            if record.date == today:
                today_diet = record
            if record.date == target_date:
                day_diet = record
            if seven_days_ago <= record.date <= today:
                last_7_diets.append(record)

        # ── 1. Summary: averages + macro status ──
        summary = self._build_summary(all_diets, profile, target)

        # ── 2. Today's macros — always current date, not request date ──
        todays_macros = self._build_todays_macros(today_diet, target)

        # ── 3. Meals breakdown — for the requested date ──
        today_meals = self._build_day_meals(day_diet, target_date)

        # ── 4. Last 7 days calorie history ──
        last_7_days = self._build_last_7_days(last_7_diets)

        return DietReportResponse(
            data=DietReportData(
                summary=summary,
                todays_macros=todays_macros,
                today=today_meals,
                last_7_days=last_7_days,
            )
        )

    def _build_summary(self, all_diets, profile: dict, target: dict) -> ReportSummary:
        days_count = len(all_diets)
        total_nutrients = {k: 0.0 for k in MACRO_KEYS}

        for diet_record in all_diets:
            day_totals = sum_nutrients_from_meals(diet_record.diet_data, MACRO_KEYS)
            for k in MACRO_KEYS:
                total_nutrients[k] += day_totals[k]
                

        days = max(days_count, 1)
        avg = {k: round(total_nutrients[k] / days) for k in MACRO_KEYS}

        recommended = self._calculate_recommended(profile, target)

        return ReportSummary(
            days_tracked=days_count,
            avg_calories=avg["calories"],
            avg_protein=avg["protein"],
            avg_carbs=avg["carbs"],
            avg_fat=avg["fat"],
            calories_status=MacroStatus(
                actual_avg=avg["calories"],
                recommended=recommended["calories"],
                status=self._get_status(avg["calories"], recommended["calories"]),
            ),
            protein_status=MacroStatus(
                actual_avg=avg["protein"],
                recommended=recommended["protein"],
                status=self._get_status(avg["protein"], recommended["protein"]),
            ),
            carbs_status=MacroStatus(
                actual_avg=avg["carbs"],
                recommended=recommended["carbs"],
                status=self._get_status(avg["carbs"], recommended["carbs"]),
            ),
            fat_status=MacroStatus(
                actual_avg=avg["fat"],
                recommended=recommended["fat"],
                status=self._get_status(avg["fat"], recommended["fat"]),
            ),
        )

    def _calculate_recommended(self, profile: dict, target: dict) -> dict:
        """Use same logic as get_macros / calculate_macros API."""
        if target and target.get("calories"):
            return {
                "calories": target.get("calories") or 0,
                "protein": target.get("protein") or 0,
                "carbs": target.get("carbs") or 0,
                "fat": target.get("fat") or 0,
            }

        weight = profile.get("weight")
        height = profile.get("height")
        age = profile.get("age")
        if not weight or not height or not age:
            return {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

        gender = (profile.get("gender") or "male").lower()
        bmr = calculate_bmr(weight, height, age, gender)

        lifestyle = (profile.get("lifestyle") or "sedentary").lower()
        multiplier = ACTIVITY_MULTIPLIERS.get(lifestyle, 1.2)
        tdee = bmr * multiplier

        goals = (profile.get("goals") or "maintenance").lower()
        if goals == "weight_loss":
            tdee = round(tdee * 0.80)
        elif goals == "weight_gain":
            tdee = round(tdee * 1.20)
        else:
            tdee = round(tdee)

        protein, carbs, fat, _, _ = calculate_macros(tdee, goals, lifestyle)

        return {
            "calories": int(tdee),
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
        }

    @staticmethod
    def _build_todays_macros(day_diet, target: dict) -> TodaysMacros:
        """Same logic as get_macros_micros — target vs actual + remaining + micros %."""
        all_keys = MACRO_MICRO_KEYS + MICRO_KEYS
        totals = sum_nutrients_from_meals(
            day_diet.diet_data if day_diet else None,
            all_keys,
        )

        overall = {}
        remaining = {}

        for key in MACRO_MICRO_KEYS:
            target_val = round(target.get(key) or 0) if target else 0
            actual_val = round(totals[key])
            overall[key] = {"target": target_val, "actual": actual_val}
            remaining[key] = max(target_val - actual_val, 0)

        micros = {}
        for key in MICRO_KEYS:
            actual_val = round(totals[key])
            rda = MICRO_RDA[key]
            percentage = min(round((actual_val / rda) * 100), 100) if rda else 0
            micros[key] = {"actual": actual_val, "percentage": percentage}

        return TodaysMacros(overall=overall, remaining=remaining, micros=micros)

    @staticmethod
    def _get_status(actual: int, recommended: int) -> str:
        if recommended == 0:
            return "on_track"
        ratio = actual / recommended
        if ratio > 1.10:
            return "high"
        elif ratio < 0.90:
            return "low"
        return "on_track"

    # map all meal titles into 4 buckets
    MEAL_BUCKET_MAP = {
        "breakfast": "breakfast",
        "pre-breakfast / pre-meal starter": "breakfast",
        "pre workout": "breakfast",
        "post workout": "breakfast",
        "early morning detox": "breakfast",
        "lunch": "lunch",
        "mid-morning snack": "lunch",
        "dinner": "dinner",
        "bed time": "dinner",
        "snacks": "snacks",
        "evening snack": "snacks",
    }

    @staticmethod
    def _build_day_meals(day_diet, target_date: date) -> DayMeals:
        meals = {"breakfast": [], "lunch": [], "dinner": [], "snacks": []}
        custom_meals: list = []
        total_calories = 0

        if day_diet and day_diet.diet_data:
            for meal in day_diet.diet_data:
                if not isinstance(meal, dict):
                    continue
                food_list = meal.get("foodList", [])
                if not food_list:
                    continue

                raw_title = meal.get("title") or ""
                lookup_key = raw_title.strip().lower()
                bucket = DietReportService.MEAL_BUCKET_MAP.get(lookup_key)

                if bucket:
                    meals[bucket].extend(food_list)
                else:
                    # Unknown title — keep it as-is instead of dumping into snacks
                    custom_meals.append({
                        "title": raw_title,
                        "foodList": food_list,
                    })

            # total from all meals (same as last_7_days calculation)
            totals = sum_nutrients_from_meals(day_diet.diet_data, ["calories"])
            total_calories = round(totals["calories"])

        return DayMeals(
            date=target_date,
            breakfast=meals["breakfast"],
            lunch=meals["lunch"],
            dinner=meals["dinner"],
            snacks=meals["snacks"],
            custom_meals=custom_meals,
            total_calories=total_calories,
        )

    @staticmethod
    def _build_last_7_days(last_7_diets) -> list:
        result = []
        for record in last_7_diets:
            day_totals = sum_nutrients_from_meals(record.diet_data, ["calories"])
            result.append(
                DailyCalories(
                    date=record.date,
                    calories=round(day_totals["calories"]),
                )
            )
        return result
