"""DB access for pre-login nutrition ad tracking."""

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nutrition_models import NutritionAd


class NutritionAdRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_visit(
        self,
        *,
        visitor_id: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
        referrer: Optional[str],
        accept_language: Optional[str],
    ) -> NutritionAd:
        row = NutritionAd(
            visitor_id=visitor_id,
            ip_address=ip_address,
            user_agent=user_agent,
            referrer=referrer,
            accept_language=accept_language,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row
