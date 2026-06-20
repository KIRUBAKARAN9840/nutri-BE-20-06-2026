"""Database queries for Diet Personal Templates.

Only data access lives here — no business logic.
All write queries are scoped to client_id for ownership safety.
"""

from typing import List, Optional

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import ClientDietTemplate, Food

COMMON_FOOD_IDS = [
    6257, 17201, 7, 17206, 20, 1556, 654, 48, 63, 771,
    1239, 10477, 110, 15746,
]

SEARCH_LIMIT = 25


class PersonalTemplateRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_personal_templates(self, client_id: int) -> List[ClientDietTemplate]:
        result = await self.db.execute(
            select(ClientDietTemplate).where(ClientDietTemplate.client_id == client_id)
        )
        return result.scalars().all()

    async def get_template_by_id_and_client(self, template_id: int, client_id: int) -> Optional[ClientDietTemplate]:
        result = await self.db.execute(
            select(ClientDietTemplate).where(
                ClientDietTemplate.id == template_id,
                ClientDietTemplate.client_id == client_id,
            )
        )
        return result.scalars().first()

    async def check_duplicate_name(self, client_id: int, template_name: str) -> bool:
        result = await self.db.execute(
            select(ClientDietTemplate.id).where(
                ClientDietTemplate.client_id == client_id,
                ClientDietTemplate.template_name == template_name,
            )
        )
        return result.scalars().first() is not None

    async def create_template(self, client_id: int, template_name: str, diet_data: list) -> ClientDietTemplate:
        template = ClientDietTemplate(
            client_id=client_id,
            template_name=template_name,
            diet_data=diet_data,
        )
        self.db.add(template)
        await self.db.commit()
        await self.db.refresh(template)
        return template

    async def update_template_data(self, template_id: int, client_id: int, diet_data: list) -> bool:
        result = await self.db.execute(
            update(ClientDietTemplate)
            .where(ClientDietTemplate.id == template_id, ClientDietTemplate.client_id == client_id)
            .values(diet_data=diet_data)
        )
        await self.db.commit()
        return result.rowcount > 0

    async def update_template_name(self, template_id: int, client_id: int, template_name: str) -> bool:
        result = await self.db.execute(
            update(ClientDietTemplate)
            .where(ClientDietTemplate.id == template_id, ClientDietTemplate.client_id == client_id)
            .values(template_name=template_name)
        )
        await self.db.commit()
        return result.rowcount > 0

    async def delete_template(self, template_id: int, client_id: int) -> bool:
        result = await self.db.execute(
            delete(ClientDietTemplate).where(
                ClientDietTemplate.id == template_id,
                ClientDietTemplate.client_id == client_id,
            )
        )
        await self.db.commit()
        return result.rowcount > 0

    # ── Common Food Queries ──────────────────────────────────────

    async def get_common_foods(self) -> List[Food]:
        result = await self.db.execute(
            select(Food).where(Food.id.in_(COMMON_FOOD_IDS))
        )
        return result.scalars().all()

    async def search_foods(self, query: str) -> List[Food]:
        prefix_result = await self.db.execute(
            select(Food).where(Food.item.ilike(f"{query}%")).limit(SEARCH_LIMIT)
        )
        foods = prefix_result.scalars().all()
        if foods:
            return foods

        contains_result = await self.db.execute(
            select(Food).where(Food.item.ilike(f"%{query}%")).limit(SEARCH_LIMIT)
        )
        return contains_result.scalars().all()
