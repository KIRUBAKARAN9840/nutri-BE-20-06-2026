

from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_sessionmaker
from app.models.nutrition_models import AiDietBooking, AiDietCoach, Video


class NutritionPageRepository:

    async def fetch_active_ai_booking(
        self, session: AsyncSession, client_id: int
    ) -> Optional[AiDietBooking]:
      
        stmt = (
            select(AiDietBooking)
            .where(
                AiDietBooking.client_id == client_id,
                AiDietBooking.status == "active",
                AiDietBooking.expires_at > datetime.now(),
            )
            .order_by(AiDietBooking.created_at.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def has_recent_ai_plan(
        self,
        session: AsyncSession,
        client_id: int,
        *,
        lookback_days: int = 45,
    ) -> bool:
        """True if any ai_diet_coach row was generated in the last `lookback_days`."""
        cutoff = datetime.now() - timedelta(days=lookback_days)
        stmt = (
            select(AiDietCoach.id)
            .where(
                AiDietCoach.client_id == client_id,
                AiDietCoach.created_at >= cutoff,
            )
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def fetch_videos(self) -> Dict[str, str]:
        """Return {type: link} from nutrition.videos.

        Opens its own DB session so it's safe inside asyncio.gather.
        """
        AsyncSessionLocal = get_async_sessionmaker()
        async with AsyncSessionLocal() as session:
            stmt = select(Video.type, Video.link)
            rows = (await session.execute(stmt)).all()
            return {row[0]: row[1] for row in rows}
