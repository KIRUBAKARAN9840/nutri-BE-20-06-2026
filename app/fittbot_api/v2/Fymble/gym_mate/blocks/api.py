from typing import Callable, List, Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import BlockedUserDTO
from ._events import EventBus, NoopEventBus


class BlocksAPI(Protocol):
    async def block(self, blocker_id: int, blocked_id: int) -> None: ...
    async def unblock(self, blocker_id: int, blocked_id: int) -> None: ...
    async def list_blocked(self, blocker_id: int) -> List[BlockedUserDTO]: ...
    async def is_blocked_either_way(self, a: int, b: int) -> bool: ...
    async def get_blocked_ids(self, blocker_id: int) -> List[int]: ...


def build_blocks_api(
    db: AsyncSession,
    redis: Optional[Redis] = None,
    *,
    event_bus: Optional[EventBus] = None,
    on_change: Optional[Callable] = None,
) -> BlocksAPI:
    from ._repository import BlockRepository
    from ._service import BlockService

    return BlockService(
        repository=BlockRepository(db),
        event_bus=event_bus or NoopEventBus(),
        on_change=on_change,
    )
