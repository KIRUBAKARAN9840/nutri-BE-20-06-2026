"""Business logic for registration steps.

Orchestrates repository calls. Single commit per request.
"""

from __future__ import annotations

import logging
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException

from app.services.nutrition_calculator import (
    activity_multipliers,
    calculate_age,
    calculate_bmi,
    calculate_bmr,
    calculate_macros_simple as calculate_macros,
)

from .repository import RegistrationStepsRepository
from .schemas import (
    DOBStepRequest,
    GoalStepRequest,
    HeightStepRequest,
    WeightStepRequest,
    BodyShapeStepRequest,
    LifestyleStepRequest,
)

logger = logging.getLogger("registration_steps_service")


def _water_intake_for_gender(gender: str) -> float:
    g = (gender or "").lower()
    if g == "male":
        return 3.7
    if g == "female":
        return 2.7
    return 3.0


class RegistrationStepsService:
    """All registration-step business logic."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = RegistrationStepsRepository(db, redis)

    # ── Step 1: DOB ──────────────────────────────────────────────────

    async def update_dob(self, client_id: int, req: DOBStepRequest) -> dict:
        client = await self._get_verified_client(client_id)
        dob = datetime.strptime(req.dob, "%Y-%m-%d").date()
        client.dob = dob
        client.age = calculate_age(dob)
        await self.repo.commit()
        return {"status": 200, "message": "DOB updated successfully"}

    # ── Step 2: Goal ─────────────────────────────────────────────────

    async def update_goal(self, client_id: int, req: GoalStepRequest) -> dict:
        client = await self._get_verified_client(client_id)
        client.goals = req.goal
        await self.repo.commit()
        return {"status": 200, "message": "Goal updated successfully", "data": {"goal": req.goal}}

    # ── Step 3: Height ───────────────────────────────────────────────

    async def update_height(self, client_id: int, req: HeightStepRequest) -> dict:
        client = await self._get_verified_client(client_id)
        client.height = req.height
        if client.weight:
            client.bmi = calculate_bmi(client.weight, req.height)
        await self.repo.commit()
        return {
            "status": 200,
            "message": "Height updated successfully",
            "data": {"height": req.height, "bmi": client.bmi},
        }

    # ── Step 4: Weight ───────────────────────────────────────────────

    async def update_weight(self, client_id: int, req: WeightStepRequest) -> dict:
        client = await self._get_verified_client(client_id)
        client.weight = req.weight
        if client.height:
            client.bmi = calculate_bmi(req.weight, client.height)

        water = _water_intake_for_gender(client.gender)
        target = await self.repo.get_client_target(client_id)
        if not target:
            target = await self.repo.create_client_target(client_id, water)
        target.water_intake = water
        target.weight = int(req.target_weight)
        target.start_weight = req.weight

        await self.repo.commit()
        return {
            "status": 200,
            "message": "Weight updated successfully",
            "data": {
                "weight": req.weight,
                "target_weight": req.target_weight,
                "bmi": client.bmi,
            },
        }

    # ── Step 5: Body Shape ───────────────────────────────────────────

    async def update_body_shape(self, client_id: int, req: BodyShapeStepRequest) -> dict:
        await self._get_verified_client(client_id)

        combo = await self.repo.get_characters_combination(
            req.current_body_shape_id, req.target_body_shape_id
        )
        if combo:
            await self.repo.upsert_client_character(client_id, combo.id)

        await self.repo.upsert_weight_selection(
            client_id, req.current_body_shape_id, req.target_body_shape_id
        )
        await self.repo.commit()

        return {
            "status": 200,
            "message": "Body shape updated successfully",
            "data": {
                "current_body_shape_id": req.current_body_shape_id,
                "target_body_shape_id": req.target_body_shape_id,
            },
        }

    # ── Step 6: Lifestyle (final step) ───────────────────────────────

    async def update_lifestyle(self, client_id: int, req: LifestyleStepRequest) -> dict:
        client = await self._get_verified_client(client_id)
        client.lifestyle = req.lifestyle
        client.incomplete = False

        if client.weight and client.height and client.age and client.goals:
            bmr = calculate_bmr(
                weight=client.weight,
                height=client.height,
                age=client.age,
                gender=client.gender or "male",
            )
            multiplier = activity_multipliers.get(req.lifestyle, 1.2)
            tdee = bmr * multiplier

            if client.goals == "weight_loss":
                tdee -= 500
            elif client.goals == "weight_gain":
                tdee += 500

            protein, carbs, fat, fiber, sugar = calculate_macros(tdee, client.goals)

            target = await self.repo.get_client_target(client_id)
            if target:
                target.calories = int(tdee)
                target.protein = protein
                target.carbs = carbs
                target.fat = fat
                target.fiber = fiber
                target.sugar = sugar
                target.updated_at = datetime.now()

        await self.repo.commit()

        # Targeted cache clear -- no KEYS * scan
        await self.repo.clear_client_caches(client_id)

        return {
            "status": 200,
            "message": "Lifestyle updated successfully. Registration complete!",
            "data": {"lifestyle": req.lifestyle, "registration_complete": True},
        }

    # ── Steps Status ─────────────────────────────────────────────────

    async def get_steps_status(self, client_id: int) -> dict:
        client = await self._get_verified_client(client_id)

        weight_selection = await self.repo.get_weight_selection(client_id)

        return {
            "status": 200,
            "message": "Steps status retrieved successfully",
            "data": {
                "dob": client.dob is not None,
                "goal": bool(client.goals and str(client.goals).strip()),
                "height": client.height is not None,
                "weight": client.weight is not None and client.bmi is not None,
                "body_shape": weight_selection is not None,
                "lifestyle": bool(client.lifestyle and str(client.lifestyle).strip()),
                "registration_complete": not client.incomplete,
            },
        }

    # ── Private helpers ──────────────────────────────────────────────

    async def _get_verified_client(self, client_id: int):
        """Fetch client by the JWT-authenticated client_id."""
        client = await self.repo.get_client_by_id(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="CLIENT_NOT_FOUND",
            )
        return client
