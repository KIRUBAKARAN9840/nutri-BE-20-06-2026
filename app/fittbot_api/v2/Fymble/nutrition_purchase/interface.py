"""
Nutrition Purchase Module — Public Interface
=============================================
Other modules must use this class to interact with nutrition purchase functionality.
Do NOT import internal services or helpers directly.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.fittbot_api.v2.Fymble.nutrition_purchase.service import NutritionPurchaseService


class NutritionPurchaseModule:
    """Entry point for other modules to access nutrition purchase functionality."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self._service = NutritionPurchaseService(db, redis)

    async def get_preview(self, client_id: int):
        return await self._service.get_preview(client_id)

    async def get_available_dates(self):
        return await self._service.get_available_dates()

    async def get_slots_for_date(self, selected_date: date):
        return await self._service.get_slots_for_date(selected_date)
