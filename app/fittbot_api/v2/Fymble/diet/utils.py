"""Shared utilities for diet modules."""

from typing import Dict, List


def normalize_meal_title(title) -> str:
    """Normalize a meal title for matching: strip all whitespace + lowercase.

    e.g. "  Break Fast " -> "breakfast", "BREAKFAST" -> "breakfast".
    Used for matching meal slots across log_food, nutrition_diet, etc.
    """
    if not title or not isinstance(title, str):
        return ""
    return "".join(title.split()).lower()


def sum_nutrients_from_meals(diet_data: list, nutrient_keys: List[str]) -> Dict[str, float]:
    """Sum nutrient values across all meals > foodList > food items."""
    totals = {k: 0 for k in nutrient_keys}

    if not diet_data or not isinstance(diet_data, list):
        return totals

    for meal in diet_data:
        if not isinstance(meal, dict) or "foodList" not in meal:
            continue
        for food_item in meal.get("foodList", []):
            if not isinstance(food_item, dict):
                continue
            for key in nutrient_keys:
                totals[key] += food_item.get(key, 0) or 0

    return totals
