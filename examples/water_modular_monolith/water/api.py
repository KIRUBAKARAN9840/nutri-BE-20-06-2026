
from datetime import time
from typing import Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import WaterReminderCreated, WaterStatus
from ._events import EventBus


class WaterAPI(Protocol):
    """What other modules can ask the water module to do."""

    async def get_status(self, client_id: int) -> WaterStatus: ...

    async def log_glass(self, client_id: int) -> float:
        """Add 250ml. Returns the new total in litres."""
        ...

    async def set_target_litres(self, client_id: int, litres: float) -> None: ...

    async def set_reminder(
        self,
        client_id: int,
        *,
        reminder_type: str,
        is_recurring: bool,
        water_timing: float,
        intimation_start_time: time,
        intimation_end_time: time,
    ) -> WaterReminderCreated: ...

    async def delete_reminder(self, client_id: int) -> None: ...


def build_water_api(
    db: AsyncSession,
    redis: Redis,
    *,
    event_bus: Optional[EventBus] = None,
) -> WaterAPI:

    from ._cache import WaterCache
    from ._events import NoopEventBus
    from ._repository import WaterRepository
    from ._service import WaterService

    return WaterService(
        repository=WaterRepository(db),
        cache=WaterCache(redis),
        event_bus=event_bus or NoopEventBus(),
    )
