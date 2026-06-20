"""Business logic for webinar registrations."""

from typing import Tuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException

from .repository import WebinarRepository


class WebinarService:

    def __init__(self, session: AsyncSession, redis: Redis = None):
        self.session = session
        self.redis = redis

    async def register(
        self,
        *,
        name: str,
        mobile_number: str,
        gender: str,
        location: str,
        aim: str,
    ) -> Tuple[int, bool]:
        try:
            row, is_update = await WebinarRepository(self.session).upsert_by_mobile(
                name=name.strip(),
                mobile_number=mobile_number.strip(),
                gender=gender.strip(),
                location=location.strip(),
                aim=aim.strip(),
            )
            return row.id, is_update
        except Exception:
            await self.session.rollback()
            raise

    async def register_from_app(
        self,
        *,
        client_id: int,
        aim: str = "",
    ) -> Tuple[int, bool]:

        repo = WebinarRepository(self.session)
        try:
            client = await repo.fetch_client_for_registration(client_id)
            if client is None:
                raise FittbotHTTPException(
                    status_code=404,
                    detail="Client not found",
                    error_code="WEBINAR_CLIENT_NOT_FOUND",
                )
            if not client.contact or not client.contact.strip():
                raise FittbotHTTPException(
                    status_code=400,
                    detail="Client has no contact number on file",
                    error_code="WEBINAR_CLIENT_MISSING_CONTACT",
                )

            row, is_update = await repo.upsert_by_mobile(
                name=(client.name or "").strip() or "Fymble User",
                mobile_number=client.contact.strip(),
                gender=(client.gender or "Other").strip(),
                location=(client.location or "").strip() or "Unknown",
                aim=(aim or "").strip(),
                client_id=client_id,
            )
        except FittbotHTTPException:
            raise
        except Exception:
            await self.session.rollback()
            raise

        # Best-effort: invalidate home user_state so the next home call hides
        # the webinar promo card immediately (otherwise stale for up to 60s).
        if self.redis is not None:
            try:
                await self.redis.delete(f"home:v2:ustate:{client_id}")
            except Exception:
                pass

        return row.id, is_update
