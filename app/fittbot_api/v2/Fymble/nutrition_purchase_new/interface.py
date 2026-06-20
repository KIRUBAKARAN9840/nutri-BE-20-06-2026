"""Public interface for inter-module access to nutrition_purchase_new."""

from datetime import date
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from redis import asyncio as aioredis

from .service import NutritionPurchaseNewService


class NutritionPurchaseNewModule:
    """Wraps service for clean inter-module access."""

    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self._svc = NutritionPurchaseNewService(db, redis)

    async def get_preview(self) -> dict:
        return await self._svc.get_preview()

    async def get_package_status(self, client_id: int) -> dict:
        return await self._svc.get_package_status(client_id)

    async def get_available_dates(self) -> dict:
        return await self._svc.get_available_dates()

    async def get_slots_for_date(self, client_id: int, selected_date: date) -> dict:
        return await self._svc.get_slots_for_date(client_id, selected_date)
