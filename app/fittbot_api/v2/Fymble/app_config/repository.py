"""Database queries for app config (maintenance, redirect, version)."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import AppRedirect, AppVersion


class AppConfigRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_redirect(self, app: str) -> Optional[AppRedirect]:

        stmt = (
            select(AppRedirect)
            .where(AppRedirect.app == app, AppRedirect.show == True)
            .order_by(AppRedirect.type.asc())
            .limit(1)
        )
        
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_version_record(self, platform: str) -> Optional[AppVersion]:
        """Get version config for the given platform key."""
        stmt = select(AppVersion).where(AppVersion.platform == platform)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
