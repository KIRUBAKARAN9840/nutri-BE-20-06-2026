from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FriendRequestSent:
    request_id: int
    from_client_id: int
    to_client_id: int


@dataclass(frozen=True)
class FriendRequestAccepted:
    request_id: int
    from_client_id: int
    to_client_id: int


@dataclass(frozen=True)
class FriendRequestRejected:
    request_id: int
    from_client_id: int
    to_client_id: int


@dataclass(frozen=True)
class FriendRequestCancelled:
    request_id: int
    from_client_id: int
    to_client_id: int


@dataclass(frozen=True)
class FriendAdded:
    client_a_id: int
    client_b_id: int


@dataclass(frozen=True)
class FriendRemoved:
    client_a_id: int
    client_b_id: int


class EventBus(Protocol):
    async def publish(self, event: object) -> None: ...


class NoopEventBus:
    async def publish(self, event: object) -> None:
        return None
