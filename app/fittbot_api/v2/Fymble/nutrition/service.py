
import asyncio

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v2.Fymble.home.repository import HomeRepository
from app.fittbot_api.v2.Fymble.home.schemas import NutritionPackageCard

from .repository import NutritionPageRepository
from .schemas import NutritionPageResponse, PersonalStatus


class NutritionPageService:

    def __init__(self, session: AsyncSession, redis: Redis):
        self.session = session
        self.redis = redis
        self.ai_repo = NutritionPageRepository()
        self.home_repo = HomeRepository(session, redis)

    async def get_page(self, client_id: int) -> NutritionPageResponse:

        videos_task = asyncio.create_task(self.ai_repo.fetch_videos())
        nutrition_status = await self.home_repo.fetch_nutrition_status(client_id)
        nutrition_package = await self.home_repo.fetch_nutrition_package_status(client_id)
        ai_combined = await self._fetch_ai_state(client_id)
        videos = await videos_task
        nutr_purchased, diet_assigned, not_attended, nutr_schedule, nutr_booking_id = nutrition_status
        ai_booking, has_recent = ai_combined
        ai = ai_booking is not None

        create_plan = (not has_recent) if ai else False

        personal_status = PersonalStatus(
            nutrition_purchased=nutr_purchased,
            diet_plan_assigned=diet_assigned,
            not_attended=not_attended,
            nutrition_booking_id=nutr_booking_id,
            nutrition_schedule=nutr_schedule,
            nutrition_package=(
                NutritionPackageCard(**nutrition_package)
                if nutrition_package else None
            ),
        )

        return NutritionPageResponse(
            ai=ai,
            personal=nutr_purchased,
            create_plan=create_plan,
            personal_status=personal_status,
            video=videos,
        )

    async def _fetch_ai_state(self, client_id: int):
        booking = await self.ai_repo.fetch_active_ai_booking(self.session, client_id)
        has_recent = await self.ai_repo.has_recent_ai_plan(
            self.session, client_id, lookback_days=45,
        )
        return booking, has_recent
    

