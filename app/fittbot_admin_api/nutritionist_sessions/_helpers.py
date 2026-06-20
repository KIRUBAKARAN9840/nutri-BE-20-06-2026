from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.adminmodels import Admins
from app.models.nutrition_models import Nutritionist


async def resolve_nutritionist_id(admin: Admins, db: AsyncSession) -> int:
    active = await db.execute(
        select(Nutritionist.id).where(
            and_(
                Nutritionist.contact == admin.contact_number,
                Nutritionist.is_active == True,
            )
        )
    )
    nid = active.scalar()
    if nid:
        return nid

    any_match = await db.execute(
        select(Nutritionist.id).where(Nutritionist.contact == admin.contact_number)
    )
    nid = any_match.scalar()
    if nid:
        return nid

    return settings.nutritionist_fallback_id


async def resolve_nutritionist(admin: Admins, db: AsyncSession) -> Optional[Nutritionist]:
    active = await db.execute(
        select(Nutritionist).where(
            and_(
                Nutritionist.contact == admin.contact_number,
                Nutritionist.is_active == True,
            )
        )
    )
    nutri = active.scalar_one_or_none()
    if nutri:
        return nutri

    any_match = await db.execute(
        select(Nutritionist).where(Nutritionist.contact == admin.contact_number)
    )
    nutri = any_match.scalar_one_or_none()
    if nutri:
        return nutri

    fallback = await db.execute(
        select(Nutritionist).where(Nutritionist.id == settings.nutritionist_fallback_id)
    )
    return fallback.scalar_one_or_none()
