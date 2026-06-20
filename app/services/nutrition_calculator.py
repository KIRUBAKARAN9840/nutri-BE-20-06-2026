"""
Centralized nutrition calculation helpers.

Consolidates calculate_age, calculate_bmi, calculate_bmr, calculate_macros,
activity_multipliers, and water intake defaults that were duplicated across
registration.py, calculate_macros.py, profile_pic.py, auth.py, client.py,
and manual_client.py.
"""

from datetime import date
from typing import Optional, Tuple


# ── Age ─────────────────────────────────────────────────────

def calculate_age(dob: date) -> int:
    """Calculate age from date of birth."""
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# ── BMI ─────────────────────────────────────────────────────

def calculate_bmi(weight: float, height: float) -> float:
    """Calculate BMI from weight (kg) and height (cm)."""
    height_m = height / 100
    return round(weight / (height_m ** 2), 2)


# ── BMR (Mifflin-St Jeor) ──────────────────────────────────

def calculate_bmr(weight: float, height: float, age: int, gender: str = "male") -> float:
    """Calculate Basal Metabolic Rate using the Mifflin-St Jeor equation."""
    if gender.lower() == "male":
        return 10 * weight + 6.25 * height - 5 * age + 5
    else:
        return 10 * weight + 6.25 * height - 5 * age - 161


# ── Activity multipliers ───────────────────────────────────

ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,
    "lightly_active": 1.375,
    "moderately_active": 1.55,
    "very_active": 1.725,
    "super_active": 1.9,
}

# Keep a lowercase alias so existing `activity_multipliers[...]` references work
activity_multipliers = ACTIVITY_MULTIPLIERS


# ── Macros (simple – used in registration.py) ──────────────

def calculate_macros_simple(calories: float, goals: str) -> Tuple[int, int, int, int, int]:
    """
    Simple macro split (no lifestyle awareness).

    Returns (protein_g, carbs_g, fat_g, fiber_g, sugar_cap_g).
    """
    if goals == "weight_loss":
        carbs_kcal = calories * 0.30
        protein_kcal = calories * 0.45
        fat_kcal = calories * 0.20
    elif goals == "weight_gain":
        carbs_kcal = calories * 0.45
        protein_kcal = calories * 0.35
        fat_kcal = calories * 0.20
    else:  # maintenance / recomposition
        carbs_kcal = calories * 0.35
        protein_kcal = calories * 0.35
        fat_kcal = calories * 0.30

    carbs_g = round(carbs_kcal / 4)
    protein_g = round(protein_kcal / 4)
    fat_g = round(fat_kcal / 9)

    fiber_g = round((calories / 1000.0) * 14)
    sugar_cap_g = round((calories * 0.10) / 4)

    return protein_g, carbs_g, fat_g, fiber_g, sugar_cap_g


# ── Water intake defaults ───────────────────────────────────

def get_water_intake(gender: Optional[str]) -> float:
    """Return recommended daily water intake (litres) based on gender."""
    if gender and gender.lower() == "male":
        return 3.7
    elif gender and gender.lower() == "female":
        return 2.7
    return 3.0
