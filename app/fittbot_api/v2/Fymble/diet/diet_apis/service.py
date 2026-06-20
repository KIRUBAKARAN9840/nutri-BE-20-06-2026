"""Business logic for Diet macros.

Orchestrates diet-specific repository for target/actual data.
"""

import asyncio

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Optional

from app.utils.logging_utils import FittbotHTTPException
from app.models.async_database import get_async_sessionmaker
from app.fittbot_api.v2.Fymble.home.service import HomeService
from app.fittbot_api.v2.Fymble.home.schemas import HomeDataParams
from app.fittbot_api.v2.Fymble.home.repository import HomeRepository
from ..utils import sum_nutrients_from_meals
from .repository import DietRepository
from .schemas import (
    CheckEligibilityResponse,
    DietCoachFoodItem,
    DietCoachFoodsResponse,
    GetMacrosMicrosResponse,
    MessageResponse,
    SetTargetRequest,
)

MACRO_KEYS = ["calories", "protein", "carbs", "fat", "fiber", "sugar"]


class DietService:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.diet_repo = DietRepository(db, redis)

    async def _get_nutrition_flags(self, client_id: int) -> tuple:
        """Return (nutrition_purchased, diet_plan_assigned) for diet APIs.

        Logic:
          1. If client has ANY diet template ever → True, True
          2. If no diet template but has active package with remaining sessions → True, False
          3. Otherwise → False, False
        """
        from datetime import datetime
        from sqlalchemy import func, or_, select
        from app.models.nutrition_models import ClientDietTemplate, NutritionEligibility
        from app.models.async_database import get_async_sessionmaker

        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            # Check if any diet template was ever assigned
            diet_stmt = select(func.count()).select_from(ClientDietTemplate).where(
                ClientDietTemplate.client_id == client_id,
            )
            diet_result = await session.execute(diet_stmt)
            has_any_diet = diet_result.scalar() > 0

            if has_any_diet:
                return True, True

            # No diet → check if active package exists
            pkg_stmt = (
                select(NutritionEligibility)
                .where(
                    NutritionEligibility.client_id == client_id,
                    NutritionEligibility.source_type == "fymble_purchase",
                    NutritionEligibility.remaining_sessions > 0,
                    or_(
                        NutritionEligibility.expires_at.is_(None),
                        NutritionEligibility.expires_at >= datetime.now(),
                    ),
                )
                .limit(1)
            )
            pkg_result = await session.execute(pkg_stmt)
            has_package = pkg_result.scalar_one_or_none() is not None

            if has_package:
                return True, False

            return False, False

    async def get_macros_micros(self, client_id: int) -> GetMacrosMicrosResponse:
        cached, credits, nutrition_flags = await asyncio.gather(
            self.diet_repo.get_cached_target_actual(client_id),
            self.diet_repo.fetch_credit_balance(client_id),
            self._get_nutrition_flags(client_id),
        )
        nutr_purchased, diet_assigned = nutrition_flags

        if cached:
            return GetMacrosMicrosResponse(
                credits=credits,
                nutrition_purchased=nutr_purchased,
                diet_plan_assigned=diet_assigned,
                data=cached,
            )

        target_data = await self.diet_repo.get_client_target(client_id)
        actual_diet_record = await self.diet_repo.get_actual_diet(client_id)

        totals = sum_nutrients_from_meals(
            actual_diet_record.diet_data if actual_diet_record else None,
            MACRO_KEYS,
        )

        overall = {}
        remaining = {}

        for key in MACRO_KEYS:
            target = getattr(target_data, key, None) if target_data else None
            actual = round(totals[key])
            target_val = round(target) if target is not None else 0
            overall[key] = {"target": target_val, "actual": actual}
            remaining[key] = max(target_val - actual, 0)

        data = {"overall": overall, "remaining": remaining}

        await self.diet_repo.cache_target_actual(client_id, data)

        return GetMacrosMicrosResponse(
            credits=credits,
            nutrition_purchased=nutr_purchased,
            diet_plan_assigned=diet_assigned,
            data=data,
        )

    async def check_eligibility(
        self, client_id: int, client_lat: Optional[float], client_lng: Optional[float],
    ) -> CheckEligibilityResponse:
        # Always fetch credits (+ unlimited pass) + nutrition flags
        tasks = [
            self.diet_repo.fetch_credit_balance_and_unlimited(client_id),
            self._get_nutrition_flags(client_id),
        ]

        # Only fetch sessions when lat/lng provided
        if client_lat is not None and client_lng is not None:
            async def _fetch_sessions():
                AsyncSessionLocal = get_async_sessionmaker()
                async with AsyncSessionLocal() as session:
                    home_svc = HomeService(session, self.diet_repo.redis)
                    home_data = await home_svc.get_home_data(HomeDataParams(
                        client_id=client_id,
                        client_lat=client_lat,
                        client_lng=client_lng,
                    ))
                    return home_data.nearby_sessions

            tasks.append(_fetch_sessions())

        results = await asyncio.gather(*tasks)

        credits, is_unlimited = results[0]
        nutr_purchased = results[1][0]  # first element of nutrition_flags tuple
        nearby_sessions = results[2] if len(results) == 3 else []

        return CheckEligibilityResponse(
            # Unlimited-pass holders are always eligible regardless of balance.
            eligibility=is_unlimited or credits >= 1,
            credits=credits,
            is_unlimited=is_unlimited,
            nutrition_purchased=nutr_purchased,
            nearby_sessions=nearby_sessions,
        )

    async def get_foods_by_preference(self, preference: str) -> DietCoachFoodsResponse:
        if not preference:
            raise FittbotHTTPException(
                status_code=400,
                detail="preference is required",
                error_code="DIET_COACH_FOODS_MISSING_PREFERENCE",
            )
        rows = await self.diet_repo.get_foods_by_preference(preference)
        items = [
            DietCoachFoodItem(
                id=row.id,
                label=row.img_name or "",
                image_url=row.img_url,
            )
            for row in rows
        ]
        return DietCoachFoodsResponse(preference=preference, data=items)

    async def set_target(self, client_id: int, request: SetTargetRequest) -> MessageResponse:
        values = {k: v for k, v in request.model_dump().items() if v is not None}
        if not values:
            raise FittbotHTTPException(
                status_code=400,
                detail="At least one macro target must be provided",
                error_code="DIET_TARGET_EMPTY",
                log_data={"client_id": client_id},
            )
        await self.diet_repo.upsert_client_target(client_id, values)
        await self.diet_repo.invalidate_target_actual_cache(client_id)
        await self.diet_repo.invalidate_report_info_cache(client_id)
        return MessageResponse(message="Target updated successfully")
