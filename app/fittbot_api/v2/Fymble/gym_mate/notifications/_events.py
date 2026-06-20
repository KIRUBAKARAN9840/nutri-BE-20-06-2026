"""Domain events emitted by the notifications module itself.

These are for downstream consumers (analytics, audit logs, etc.) — the
notifications module's primary job is to SUBSCRIBE to events from other
modules (friends, sessions, chat, stories), not to emit many of its own.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol


@dataclass
class NotificationCreated:
    notification_id: int
    recipient_client_id: int
    category: str
    created_at: datetime


@dataclass
class PushDispatched:
    """A FCM multicast was sent to the broker. Doesn't guarantee
    delivery — see PushDelivered / PushFailed for that."""
    notification_id: int
    recipient_client_id: int
    token_count: int


@dataclass
class PushDelivered:
    notification_id: int
    recipient_client_id: int
    success_count: int


@dataclass
class PushFailed:
    notification_id: int
    recipient_client_id: int
    error: str


class EventBus(Protocol):
    async def publish(self, event: Any) -> None: ...


class NoopEventBus:
    async def publish(self, event: Any) -> None:
        return
