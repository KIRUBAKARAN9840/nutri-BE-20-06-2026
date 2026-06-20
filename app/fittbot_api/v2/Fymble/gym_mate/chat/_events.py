from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol


@dataclass
class MessageSent:
    room_id: int
    message_id: int
    sender_client_id: int
    recipient_ids: List[int]
    created_at: datetime


@dataclass
class MessageEdited:
    room_id: int
    message_id: int
    sender_client_id: int
    edited_at: datetime


@dataclass
class MessageDeleted:
    room_id: int
    message_id: int
    sender_client_id: int
    deleted_at: datetime


@dataclass
class RoomCreated:
    room_id: int
    kind: str
    session_id: Optional[int]
    participant_ids: List[int]


class EventBus(Protocol):
    async def publish(self, event) -> None: ...


class NoopEventBus:
    async def publish(self, event) -> None:
        return None
