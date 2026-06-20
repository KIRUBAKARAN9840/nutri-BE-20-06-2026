
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


# ---------------------------------------------------------------------------
# Public event types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StoryPublished:
    story_id: int
    client_id: int
    audience: str           # 'public' | 'friends'
    expires_at: datetime


@dataclass(frozen=True)
class StoryDeleted:
    story_id: int
    client_id: int


# ---------------------------------------------------------------------------
# Event bus port (same shape as profile)
# ---------------------------------------------------------------------------
class EventBus(Protocol):
    async def publish(self, event: object) -> None: ...


class NoopEventBus:
    async def publish(self, event: object) -> None:
        return None
