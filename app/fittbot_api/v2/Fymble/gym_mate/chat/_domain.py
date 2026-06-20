from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional


EDIT_WINDOW_MINUTES = 30
MAX_BODY_LEN = 4000


class ChatRoomKind(str, Enum):
    FRIEND_DIRECT = "friend_direct"
    SESSION_DIRECT = "session_direct"
    SESSION_GROUP = "session_group"


class ChatMessageKind(str, Enum):
    TEXT = "text"
    SYSTEM = "system"


class ChatDomainError(Exception):
    pass


class CannotChatSelf(ChatDomainError): ...
class NotParticipant(ChatDomainError): ...
class NotFriends(ChatDomainError): ...
class NotSessionMember(ChatDomainError): ...
class ChatBlocked(ChatDomainError): ...
class SessionChatClosed(ChatDomainError): ...
class InvalidMessageBody(ChatDomainError): ...
class EditWindowExpired(ChatDomainError): ...
class MessageAlreadyDeleted(ChatDomainError): ...
class NotMessageOwner(ChatDomainError): ...
class RoomKindMismatch(ChatDomainError): ...


@dataclass(frozen=True)
class MessageBody:
    """Validated chat message body. Strips and length-checks at construction."""
    value: str

    def __post_init__(self) -> None:
        trimmed = self.value.strip()
        if not trimmed:
            raise InvalidMessageBody("message body cannot be empty")
        if len(trimmed) > MAX_BODY_LEN:
            raise InvalidMessageBody(
                f"message body max {MAX_BODY_LEN} chars, got {len(trimmed)}"
            )
        object.__setattr__(self, "value", trimmed)


def canonical_pair_key(a: int, b: int) -> str:
    """Direct rooms use 'min-max' so a pair maps to exactly one row regardless
    of who opened the chat first."""
    if a == b:
        raise CannotChatSelf("cannot open a direct chat with yourself")
    lo, hi = (a, b) if a < b else (b, a)
    return f"{lo}-{hi}"


@dataclass
class Room:
    kind: ChatRoomKind
    session_id: Optional[int] = None
    pair_key: Optional[str] = None
    id: Optional[int] = None
    last_message_id: Optional[int] = None
    last_message_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    @classmethod
    def friend_direct(cls, a: int, b: int) -> "Room":
        return cls(
            kind=ChatRoomKind.FRIEND_DIRECT,
            pair_key=canonical_pair_key(a, b),
            session_id=None,
        )

    @classmethod
    def session_direct(cls, session_id: int, a: int, b: int) -> "Room":
        return cls(
            kind=ChatRoomKind.SESSION_DIRECT,
            pair_key=canonical_pair_key(a, b),
            session_id=session_id,
        )

    @classmethod
    def session_group(cls, session_id: int) -> "Room":
        return cls(
            kind=ChatRoomKind.SESSION_GROUP,
            session_id=session_id,
            pair_key=None,
        )

    def is_session_scoped(self) -> bool:
        return self.kind in (ChatRoomKind.SESSION_DIRECT, ChatRoomKind.SESSION_GROUP)


@dataclass
class Participant:
    room_id: int
    client_id: int
    joined_at: Optional[datetime] = None
    last_read_message_id: Optional[int] = None
    muted: bool = False
    id: Optional[int] = None


@dataclass
class Message:
    room_id: int
    sender_client_id: int
    body: MessageBody
    kind: ChatMessageKind = ChatMessageKind.TEXT
    client_msg_id: Optional[str] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    @classmethod
    def create(
        cls,
        room_id: int,
        sender_client_id: int,
        body: str,
        client_msg_id: Optional[str] = None,
        kind: ChatMessageKind = ChatMessageKind.TEXT,
    ) -> "Message":
        return cls(
            room_id=room_id,
            sender_client_id=sender_client_id,
            body=MessageBody(body),
            kind=kind,
            client_msg_id=client_msg_id,
        )

    def edit(self, actor_client_id: int, new_body: str, *, now: Optional[datetime] = None) -> None:
        if actor_client_id != self.sender_client_id:
            raise NotMessageOwner("only the sender can edit this message")
        if self.deleted_at is not None:
            raise MessageAlreadyDeleted("cannot edit a deleted message")
        now = now or datetime.now()
        if self.created_at and now - self.created_at > timedelta(minutes=EDIT_WINDOW_MINUTES):
            raise EditWindowExpired(
                f"messages can only be edited within {EDIT_WINDOW_MINUTES} minutes"
            )
        self.body = MessageBody(new_body)
        self.edited_at = now

    def soft_delete(self, actor_client_id: int, *, now: Optional[datetime] = None) -> None:
        if actor_client_id != self.sender_client_id:
            raise NotMessageOwner("only the sender can delete this message")
        if self.deleted_at is not None:
            raise MessageAlreadyDeleted("message is already deleted")
        self.deleted_at = now or datetime.now()


@dataclass
class RoomWithMembers:
    """Aggregate handy for service-layer authorization checks."""
    room: Room
    member_ids: List[int] = field(default_factory=list)

    def is_member(self, client_id: int) -> bool:
        return client_id in self.member_ids
