from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ContentReported:
    report_id: int
    reporter_client_id: int
    entity_type: str
    entity_id: int
    reason: str


class EventBus(Protocol):
    async def publish(self, event: object) -> None: ...


class NoopEventBus:
    async def publish(self, event: object) -> None:
        return None
