"""Database queries for Log Food.

Only data access lives here — no business logic.
"""

from datetime import date as date_type
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.fittbot_models import ActualDiet
from app.models.nutrition_models import NutritionDietMealLog


class LogFoodRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_actual_diet(self, client_id: int, dt: date_type) -> Optional[ActualDiet]:
        result = await self.db.execute(
            select(ActualDiet).where(
                ActualDiet.client_id == client_id,
                ActualDiet.date == dt,
            )
        )
        return result.scalars().first()

    async def create_actual_diet(self, client_id: int, dt: date_type, diet_data: list) -> ActualDiet:
        record = ActualDiet(client_id=client_id, date=dt, diet_data=diet_data)
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def update_diet_data(self, record: ActualDiet, diet_data: list) -> ActualDiet:
        record.diet_data = diet_data
        flag_modified(record, "diet_data")
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def insert_template_meal_logs(
        self,
        client_id: int,
        client_template_id: int,
        day_number: int,
        rows: List[dict],
    ) -> None:
        """Bulk-insert (client_template_id, day_number, title) markers.

        rows: list of {"title": str, "title_norm": str}
        Idempotent — duplicates against the unique constraint are ignored.
        """
        if not rows:
            return

        values = [
            {
                "client_diet_template_id": client_template_id,
                "client_id": client_id,
                "day_number": day_number,
                "title": r["title"],
                "title_norm": r["title_norm"],
            }
            for r in rows
        ]
        stmt = mysql_insert(NutritionDietMealLog).values(values)
        stmt = stmt.prefix_with("IGNORE")  # MySQL: skip rows that violate UNIQUE
        await self.db.execute(stmt)
        await self.db.commit()
