"""Database queries for Sidebar."""

from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.client import Client
from app.fittbot_api.v1.payments.models.credits import CreditBalance


class SidebarRepository:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def fetch_client_info(self, client_id: int) -> Tuple[Optional[str], Optional[str]]:
        """Return (name, contact) for a client."""
        stmt = select(Client.name, Client.contact).where(
            Client.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None, None
        return row[0], row[1]

    async def fetch_credit_balance(self, client_id: int) -> int:
        """Return current credit balance after sweeping any expired grants."""
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service_async import (
            expire_stale_credits_isolated,
        )
        await expire_stale_credits_isolated(client_id, redis=self.redis)

        stmt = select(CreditBalance.balance).where(
            CreditBalance.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        row = result.scalar()
        return row if row is not None else 0

    async def fetch_unlimited_active(self, client_id: int) -> bool:
        """True if the client holds an active unlimited-scan pass (credit_999)."""
        ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        stmt = select(CreditBalance.client_id).where(
            CreditBalance.client_id == client_id,
            CreditBalance.unlimited_until.isnot(None),
            CreditBalance.unlimited_until > ist_now,
        )
        result = await self.db.execute(stmt)
        return result.scalar() is not None
