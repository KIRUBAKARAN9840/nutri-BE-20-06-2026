"""Repository for Food Scanner database operations.

Handles all database queries for food scanning, leaderboards, and calorie tracking.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select
import pytz

from app.models.fittbot_models import (
    ActualDiet,
    ClientTarget,
    CalorieEvent,
)

# Timezone
IST = pytz.timezone("Asia/Kolkata")


def get_meal_by_current_time() -> str:
    """Determine which meal category based on current time."""
    now = datetime.now(IST)
    current_time = now.time()

    # Meal time ranges
    time_mapping = [
        (5, 30, 6, 0, "Early morning Detox"),
        (6, 30, 7, 0, "Pre workout"),
        (7, 0, 7, 30, "Pre-Breakfast / Pre-Meal Starter"),
        (7, 30, 8, 0, "Post workout"),
        (8, 30, 9, 30, "Breakfast"),
        (10, 0, 11, 0, "Mid-Morning snack"),
        (13, 0, 14, 0, "Lunch"),
        (16, 0, 17, 0, "Evening snack"),
        (19, 30, 20, 30, "Dinner"),
        (21, 30, 22, 0, "Bed time"),
    ]

    current_minutes = current_time.hour * 60 + current_time.minute

    # First check if current time falls within any meal range
    for start_h, start_m, end_h, end_m, meal_name in time_mapping:
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if start_minutes <= current_minutes <= end_minutes:
            return meal_name

    # If not in any range, find the nearest meal
    min_distance = float('inf')
    nearest_meal = "Breakfast"

    for start_h, start_m, end_h, end_m, meal_name in time_mapping:
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        midpoint_minutes = (start_minutes + end_minutes) // 2

        distance = abs(current_minutes - midpoint_minutes)
        if distance > 720:
            distance = 1440 - distance

        if distance < min_distance:
            min_distance = distance
            nearest_meal = meal_name

    return nearest_meal


def get_default_diet_structure() -> List[Dict[str, Any]]:
    """Return the default diet structure."""
    return [
        {"id": "1", "title": "Pre workout", "tagline": "Energy boost", "foodList": [], "timeRange": "6:30-7:00 AM", "itemsCount": 0},
        {"id": "2", "title": "Post workout", "tagline": "Recovery fuel", "foodList": [], "timeRange": "7:30-8:00 AM", "itemsCount": 0},
        {"id": "3", "title": "Breakfast", "tagline": "Start your day right", "foodList": [], "timeRange": "8:30-9:30 AM", "itemsCount": 0},
        {"id": "4", "title": "Mid-Morning snack", "tagline": "Keep your energy up", "foodList": [], "timeRange": "10:00-11:00 AM", "itemsCount": 0},
        {"id": "5", "title": "Lunch", "tagline": "Fuel up properly", "foodList": [], "timeRange": "1:00-2:00 PM", "itemsCount": 0},
        {"id": "6", "title": "Evening snack", "tagline": "Healthy munching", "foodList": [], "timeRange": "4:00-5:00 PM", "itemsCount": 0},
        {"id": "7", "title": "Dinner", "tagline": "End your day well", "foodList": [], "timeRange": "7:30-8:30 PM", "itemsCount": 0},
        {"id": "8", "title": "Bed time", "tagline": "Rest well", "foodList": [], "timeRange": "9:30-10:00 PM", "itemsCount": 0},
    ]


class FoodScannerRepository:
    """Repository for food scanner database operations."""

    def __init__(self, db: Session):
        self.db = db

    async def get_actual_diet(self, client_id: int, today: date) -> Optional[ActualDiet]:
        """Get actual diet record for a client on a specific date."""
        today_str = today.strftime("%Y-%m-%d")
        result = await self.db.execute(
            select(ActualDiet).where(
                ActualDiet.client_id == client_id,
                ActualDiet.date == today_str
            )
        )
        return result.scalars().first()

    async def get_client_target(self, client_id: int) -> Optional[ClientTarget]:
        """Get client target (calorie goals)."""
        result = await self.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        return result.scalars().first()

    async def save_food_to_diet(
        self,
        client_id: int,
        meal: str,
        food_item: Dict[str, Any],
        today: date
    ) -> None:
        """Save scanned food to the client's diet record."""
        today_str = today.strftime("%Y-%m-%d")
        existing_entry = await self.get_actual_diet(client_id, today)

        if existing_entry:
            diet_data = existing_entry.diet_data or []
            meal_found = False

            for meal_category in diet_data:
                if meal_category.get("title", "").lower() == meal.lower():
                    meal_category["foodList"].append(food_item)
                    meal_category["itemsCount"] = len(meal_category["foodList"])
                    meal_found = True
                    break

            if not meal_found:
                default_structure = get_default_diet_structure()
                for default_meal in default_structure:
                    if default_meal.get("title", "").lower() == meal.lower():
                        default_meal["foodList"] = [food_item]
                        default_meal["itemsCount"] = 1
                        diet_data.append(default_meal)
                        break

            from sqlalchemy.orm import attributes
            attributes.flag_modified(existing_entry, "diet_data")
            existing_entry.diet_data = diet_data
        else:
            diet_data = get_default_diet_structure()
            for meal_category in diet_data:
                if meal_category.get("title", "").lower() == meal.lower():
                    meal_category["foodList"] = [food_item]
                    meal_category["itemsCount"] = 1
                    break

            new_entry = ActualDiet(
                client_id=client_id,
                date=today_str,
                diet_data=diet_data
            )
            self.db.add(new_entry)

        await self.db.commit()

    async def get_calorie_event(self, client_id: int, today: date) -> Optional[CalorieEvent]:
        """Get calorie event for a client on a specific date."""
        result = await self.db.execute(
            select(CalorieEvent).where(
                CalorieEvent.client_id == client_id,
                CalorieEvent.event_date == today
            )
        )
        return result.scalars().first()

    async def create_or_update_calorie_event(
        self,
        client_id: int,
        today: date,
        calories_added: int = 0
    ) -> CalorieEvent:
        """Create or update calorie event for a client."""
        calorie_event = await self.get_calorie_event(client_id, today)

        if not calorie_event:
            calorie_event = CalorieEvent(
                client_id=client_id,
                event_date=today,
                calories_added=0,
            )
            self.db.add(calorie_event)
            await self.db.flush()

        if not calorie_event.calories_added:
            calorie_event.calories_added = 0

        calorie_event.calories_added += calories_added
        await self.db.commit()
        return calorie_event

    async def calculate_and_award_xp(
        self,
        client_id: int,
        today: date
    ) -> int:
        """Award 5 XP per scan, capped at 50 XP/day."""
        XP_PER_SCAN = 5
        MAX_DAILY_DIET_XP = 50

        # Get or create calorie event
        calorie_event = await self.get_calorie_event(client_id, today)
        if not calorie_event:
            calorie_event = await self.create_or_update_calorie_event(client_id, today)

        added_calories = calorie_event.calories_added or 0

        if added_calories >= MAX_DAILY_DIET_XP:
            return 0

        points_to_award = min(XP_PER_SCAN, MAX_DAILY_DIET_XP - added_calories)
        await self.create_or_update_calorie_event(client_id, today, points_to_award)
        return points_to_award


def calculate_totals(diet_data: list) -> dict:
    """Calculate total nutritional values from diet data."""
    totals = {
        "calories": 0,
        "protein": 0,
        "carbs": 0,
        "fats": 0,
        "fiber": 0,
        "sugar": 0,
    }

    if not diet_data:
        return totals

    # Handle template format (list of meals with foodList)
    if isinstance(diet_data, list) and diet_data and isinstance(diet_data[0], dict):
        if "foodList" in diet_data[0]:
            # Template format
            for meal in diet_data:
                for food in meal.get("foodList", []):
                    totals["calories"] += food.get("calories", 0)
                    totals["protein"] += food.get("protein", 0)
                    totals["carbs"] += food.get("carbs", 0)
                    totals["fats"] += food.get("fat", 0)  # Note: field name difference
                    totals["fiber"] += food.get("fiber", 0)
                    totals["sugar"] += food.get("sugar", 0)
        else:
            # Legacy format (list of food items)
            for food in diet_data:
                totals["calories"] += food.get("calories", 0)
                totals["protein"] += food.get("protein", 0)
                totals["carbs"] += food.get("carbs", 0)
                totals["fats"] += food.get("fat", 0)
                totals["fiber"] += food.get("fiber", 0)
                totals["sugar"] += food.get("sugar", 0)

    return totals
