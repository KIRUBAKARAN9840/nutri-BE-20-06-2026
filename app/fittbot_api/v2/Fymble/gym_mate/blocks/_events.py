from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UserBlocked:
    blocker_client_id: int
    blocked_client_id: int


@dataclass(frozen=True)
class UserUnblocked:
    blocker_client_id: int
    blocked_client_id: int


class EventBus(Protocol):
    async def publish(self, event: object) -> None: ...


class NoopEventBus:
    async def publish(self, event: object) -> None:
        return None
