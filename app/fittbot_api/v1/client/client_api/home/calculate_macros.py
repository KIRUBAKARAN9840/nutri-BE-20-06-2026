# app/api/v1/nutrition/calculate_calories.py

import json
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from redis.asyncio import Redis

from app.models.database import get_db
from app.utils.redis_config import get_redis
from app.utils.logging_utils import FittbotHTTPException
from app.models.fittbot_models import ClientTarget, Client
from app.services.nutrition_calculator import calculate_bmr, activity_multipliers
from app.services.cache_service import delete_keys_by_pattern

router = APIRouter(prefix=("/calculate_macros"),tags=["Nutrition"])


# def calculate_macros(calories, goals):
#     try:
#         if goals == "weight_loss":
#             carbs = calories * 0.30
#             carbs_grams = round(carbs / 4)

#             protein = calories * 0.45
#             protein_grams = round(protein / 4)

#             fat = calories * 0.2
#             fat_grams = round(fat / 9)

#         elif goals == "weight_gain":
#             carbs = calories * 0.45
#             carbs_grams = round(carbs / 4)

#             protein = calories * 0.35
#             protein_grams = round(protein / 4)

#             fat = calories * 0.2
#             fat_grams = round(fat / 9)

#         else:
#             carbs = calories * 0.35
#             carbs_grams = round(carbs / 4)

#             protein = calories * 0.35
#             protein_grams = round(protein / 4)

#             fat = calories * 0.3
#             fat_grams = round(fat / 9)

#         return protein_grams, carbs_grams, fat_grams
#     except FittbotHTTPException:
#         raise
#     except Exception as e:
#         raise FittbotHTTPException(
#             status_code=500,
#             detail=f"An unexpected error occurred {e}",
#             error_code="MACROS_CALC_ERROR",
#             log_data={"calories": calories, "goals": goals, "error": str(e)},
#         )

# keep your existing activity_multipliers and calculate_bmr()

# Macro percentages based on Goal + Lifestyle
# Format: (carbs%, protein%, fat%)
MACRO_SPLITS = {
    "weight_loss": {
        "sedentary": (0.50, 0.30, 0.20),
        "lightly_active": (0.50, 0.30, 0.20),
        "moderately_active": (0.52, 0.28, 0.20),
        "very_active": (0.55, 0.25, 0.20),
        "super_active": (0.55, 0.25, 0.20),
    },
    "weight_gain": {
        "sedentary": (0.55, 0.25, 0.20),
        "lightly_active": (0.58, 0.22, 0.20),
        "moderately_active": (0.58, 0.22, 0.20),
        "very_active": (0.60, 0.20, 0.20),
        "super_active": (0.60, 0.20, 0.20),
    },
    "maintenance": {
        "sedentary": (0.55, 0.25, 0.20),
        "lightly_active": (0.58, 0.22, 0.20),
        "moderately_active": (0.60, 0.20, 0.20),
        "very_active": (0.62, 0.18, 0.20),
        "super_active": (0.65, 0.15, 0.20),
    },
}


# Fiber per 1000 kcal based on goal
FIBER_PER_1000_KCAL = {
    "weight_loss": 20,
    "maintenance": 15,
    "weight_gain": 13,
}

# Sugar per 1000 kcal based on goal
SUGAR_PER_1000_KCAL = {
    "weight_loss": 12.5,
    "maintenance": 18,
    "weight_gain": 25,
}


def calculate_macros(calories: float, goals: str, lifestyle: str = "sedentary"):

    goal_splits = MACRO_SPLITS.get(goals, MACRO_SPLITS["maintenance"])
    carbs_pct, protein_pct, fat_pct = goal_splits.get(
        lifestyle, goal_splits["sedentary"]
    )

    # Calculate kcal from percentages
    carbs_kcal = calories * carbs_pct
    protein_kcal = calories * protein_pct
    fat_kcal = calories * fat_pct

    # Convert to grams
    carbs_g = round(carbs_kcal / 4)
    protein_g = round(protein_kcal / 4)
    fat_g = round(fat_kcal / 9)

    # Fiber based on goal (g per 1000 kcal)
    fiber_per_1000 = FIBER_PER_1000_KCAL.get(goals, 15)
    fiber_g = round((calories / 1000.0) * fiber_per_1000)

    # Sugar based on goal (g per 1000 kcal)
    sugar_per_1000 = SUGAR_PER_1000_KCAL.get(goals, 18)
    sugar_g = round((calories / 1000.0) * sugar_per_1000)

    return protein_g, carbs_g, fat_g, fiber_g, sugar_g

# ---- Request schema (unchanged) ----
class CaloriesData(BaseModel):
    client_id: int
    height: float
    weight: float
    age: int
    goals: str
    lifestyle: str


# ---- Endpoint (logic unchanged, errors normalized) ----
@router.post("/calculate")
async def calculate_calories(
    data: CaloriesData,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    try:
        bmr = calculate_bmr(data.weight, data.height, data.age)  # default gender='male' preserved

        tdee = bmr * activity_multipliers[data.lifestyle]

        # Apply 20% calorie adjustment based on goal
        if data.goals == "weight_loss":
            tdee = round(tdee * 0.80)  # 20% deficit
        elif data.goals == "weight_gain":
            tdee = round(tdee * 1.20)  # 20% surplus
        else:
            tdee = round(tdee)  # maintenance: no change, just round

        protein, carbs, fat, fiber, sugar = calculate_macros(tdee, data.goals, data.lifestyle)

        client_target = db.query(ClientTarget).filter(ClientTarget.client_id == data.client_id).first()
        client = db.query(Client).filter(Client.client_id == data.client_id).first()
        client.weight = data.weight
        client.height = data.height
        client.age = data.age
        client.goals = data.goals
        client.lifestyle = data.lifestyle
        db.commit()
        db.refresh(client)

        if client_target:
            client_target.calories = int(tdee)
            client_target.protein = protein
            client_target.carbs = carbs
            client_target.fat = fat
            client_target.fiber=fiber
            client_target.sugar=sugar
            client_target.updated_at = datetime.now()
            db.commit()
        else:
            client_target = ClientTarget(
                client_id=data.client_id,
                calories=int(tdee),
                protein=protein,
                carbs=carbs,
                fat=fat,
                updated_at=datetime.now(),
            )
            db.add(client_target)
            db.commit()

        await delete_keys_by_pattern(redis, "*:initial_target_actual")
        await delete_keys_by_pattern(redis, "*:initialstatus")
        await delete_keys_by_pattern(redis, "*:target_actual")

        return {
            "status": 200,
            "message": "Calories calculated successfully",
            "data": {
                "client_id": data.client_id,
                "calories": int(tdee),
                "protein": protein,
                "carbs": carbs,
                "fat": fat,
                "fiber":fiber,
                "sugar":sugar
            },
        }
    except FittbotHTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise FittbotHTTPException(
            status_code=400,
            detail=f"Error: {str(e)}",
            error_code="CALORIES_CALC_ERROR",
            log_data={
                "client_id": data.client_id,
                "lifestyle": data.lifestyle,
                "goals": data.goals,
                "error": str(e),
            },
        )
