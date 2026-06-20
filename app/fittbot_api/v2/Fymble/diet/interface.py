"""
Diet Module — Public Interface
===============================
Other modules must use these classes to interact with diet functionality.
Do NOT import repository or internal helpers directly.
"""

from datetime import date
from typing import Optional, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.fittbot_api.v2.Fymble.diet.diet_apis.service import DietService
from app.fittbot_api.v2.Fymble.diet.personal_template.service import PersonalTemplateService
from app.fittbot_api.v2.Fymble.diet.log_food.service import LogFoodService
from app.fittbot_api.v2.Fymble.diet.diet_report.service import DietReportService


class DietModule:
    """Entry point for other modules to access diet functionality."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self._diet_service = DietService(db, redis)
        self._template_service = PersonalTemplateService(db)
        self._log_food_service = LogFoodService(db, redis)
        self._report_service = DietReportService(db, redis)

    # ── Diet Targets & Actuals ──

    async def get_macros_micros(self, client_id: int):
        return await self._diet_service.get_macros_micros(client_id)

    async def set_target(self, client_id: int, request):
        return await self._diet_service.set_target(client_id, request)

    # ── Personal Templates ──

    async def list_templates(self, client_id: int):
        return await self._template_service.list_templates(client_id)

    async def get_template(self, client_id: int, template_id: int):
        return await self._template_service.get_template(client_id, template_id)

    async def search_foods(self, query: str):
        return await self._template_service.search_foods(query)

    async def get_common_foods(self):
        return await self._template_service.get_common_foods()

    # ── Log Food ──

    async def add_food(self, client_id: int, request):
        return await self._log_food_service.add_food(client_id, request)

    # ── Diet Report ──

    async def get_diet_report(self, client_id: int, report_date: Optional[date] = None):
        return await self._report_service.get_report(client_id, report_date)
