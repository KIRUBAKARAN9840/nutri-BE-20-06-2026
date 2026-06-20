"""Business logic for nutrition ad visit tracking.

Owns its own DB session so a failure in get_async_db cannot bubble up to the
caller — the route wraps the whole thing in try/except anyway, but isolating
the session here means a connection-pool error is just one more thing the
route's except block swallows.
"""

from typing import Optional

from app.models.async_database import get_async_sessionmaker

from .repository import NutritionAdRepository


class NutritionAdService:

    async def record_visit(
        self,
        *,
        visitor_id: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
        referrer: Optional[str],
        accept_language: Optional[str],
    ) -> Optional[int]:
        SessionLocal = get_async_sessionmaker()
        async with SessionLocal() as session:
            try:
                row = await NutritionAdRepository(session).record_visit(
                    visitor_id=visitor_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    referrer=referrer,
                    accept_language=accept_language,
                )
                return row.id
            except Exception:
                await session.rollback()
                raise
