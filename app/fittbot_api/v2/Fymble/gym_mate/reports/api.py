from typing import Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import ReportSubmittedDTO
from ._events import EventBus, NoopEventBus


class ReportsAPI(Protocol):
    async def submit(
        self,
        reporter_id: int,
        entity_type: str,
        entity_id: int,
        reason: str,
        details: Optional[str] = None,
    ) -> ReportSubmittedDTO: ...


def build_reports_api(
    db: AsyncSession,
    redis: Optional[Redis] = None,
    *,
    event_bus: Optional[EventBus] = None,
) -> ReportsAPI:
    from ._repository import ReportRepository
    from ._service import ReportService

    return ReportService(
        repository=ReportRepository(db),
        event_bus=event_bus or NoopEventBus(),
    )
