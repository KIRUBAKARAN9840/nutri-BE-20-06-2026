from dataclasses import dataclass
from datetime import date, time
from typing import Protocol


@dataclass(frozen=True)
class SessionCreated:
    session_id: int
    host_client_id: int
    gym_id: int
    session_date: date
    session_time: time
    payment_mode: str
    payment_status: str


@dataclass(frozen=True)
class SessionCancelled:
    session_id: int
    host_client_id: int


@dataclass(frozen=True)
class SessionPaid:
    session_id: int
    host_client_id: int
    daily_pass_id: str


@dataclass(frozen=True)
class SessionRequestCreated:
    request_id: int
    session_id: int
    host_client_id: int
    requester_client_id: int


@dataclass(frozen=True)
class SessionRequestAccepted:
    request_id: int
    session_id: int
    host_client_id: int
    requester_client_id: int


@dataclass(frozen=True)
class SessionRequestRejected:
    request_id: int
    session_id: int
    host_client_id: int
    requester_client_id: int


@dataclass(frozen=True)
class SessionRequestWithdrawn:
    request_id: int
    session_id: int
    host_client_id: int
    requester_client_id: int


class EventBus(Protocol):
    async def publish(self, event: object) -> None: ...


class NoopEventBus:
    async def publish(self, event: object) -> None:
        return None
