"""Business logic for Sidebar."""

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .repository import SidebarRepository
from .schemas import SidebarDataResponse


class SidebarService:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = SidebarRepository(db, redis)

    async def get_sidebar_data(self, client_id: int) -> SidebarDataResponse:
        name, contact = await self.repo.fetch_client_info(client_id)
        credits = await self.repo.fetch_credit_balance(client_id)
        is_unlimited = await self.repo.fetch_unlimited_active(client_id)
        return SidebarDataResponse(
            client_name=name,
            phone_number=contact,
            credits=credits,
            is_unlimited=is_unlimited,
        )
