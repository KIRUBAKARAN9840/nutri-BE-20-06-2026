"""DB access for webinar registrations."""

from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import Client
from app.models.nutrition_models import WebinarRegistration


class WebinarRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_by_mobile(
        self,
        *,
        name: str,
        mobile_number: str,
        gender: str,
        location: str,
        aim: str,
        client_id: Optional[int] = None,
    ) -> Tuple[WebinarRegistration, bool]:
        result = await self.session.execute(
            select(WebinarRegistration).where(
                WebinarRegistration.mobile_number == mobile_number
            )
        )
        existing: Optional[WebinarRegistration] = result.scalar_one_or_none()

        if existing is not None:
            existing.name = name
            existing.gender = gender
            existing.location = location
            existing.aim = aim
            if client_id is not None and existing.client_id is None:
                existing.client_id = client_id
            await self.session.commit()
            await self.session.refresh(existing)
            return existing, True

        row = WebinarRegistration(
            client_id=client_id,
            name=name,
            mobile_number=mobile_number,
            gender=gender,
            location=location,
            aim=aim,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row, False

    async def fetch_client_for_registration(
        self, client_id: int,
    ) -> Optional[Client]:
        """Return the Client row used to populate a webinar registration."""
        result = await self.session.execute(
            select(Client).where(Client.client_id == client_id)
        )
        return result.scalar_one_or_none()

    async def is_client_registered(self, client_id: int) -> bool:
        """True if any webinar row is linked to this client_id."""
        result = await self.session.execute(
            select(WebinarRegistration.id)
            .where(WebinarRegistration.client_id == client_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
