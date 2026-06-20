from typing import List, Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from . import schemas as dto
from ._events import EventBus, NoopEventBus


class ChatAPI(Protocol):
    async def open_direct_room(
        self,
        viewer_client_id: int,
        peer_client_id: int,
        session_id: Optional[int] = None,
    ) -> dto.RoomDTO: ...

    async def open_session_group_room(
        self, viewer_client_id: int, session_id: int,
    ) -> dto.RoomDTO: ...

    async def get_room(
        self, viewer_client_id: int, room_id: int,
    ) -> dto.RoomDTO: ...

    async def list_inbox(
        self,
        client_id: int,
        before_at=None,
        limit: int = 30,
    ) -> dto.InboxPageDTO: ...

    async def send_message(
        self,
        sender_client_id: int,
        room_id: int,
        body: str,
        client_msg_id: Optional[str] = None,
    ) -> dto.MessageDTO: ...

    async def edit_message(
        self, sender_client_id: int, message_id: int, body: str,
    ) -> dto.MessageDTO: ...

    async def delete_message(
        self, sender_client_id: int, message_id: int,
    ) -> None: ...

    async def delete_messages_bulk(
        self, sender_client_id: int, message_ids: list[int],
    ) -> dict: ...

    async def leave_session_group(
        self, viewer_client_id: int, room_id: int,
    ) -> None: ...

    async def report_room(
        self,
        reporter_client_id: int,
        room_id: int,
        reason: str,
        details: Optional[str] = None,
    ) -> None: ...

    async def peer_of_room(
        self, room_id: int, viewer_client_id: int,
    ) -> Optional[int]: ...

    async def list_history(
        self,
        viewer_client_id: int,
        room_id: int,
        before: Optional[int] = None,
        limit: int = 50,
    ) -> List[dto.MessageDTO]: ...

    async def mark_read(
        self, viewer_client_id: int, room_id: int, up_to_message_id: int,
    ) -> None: ...

    async def typing(self, viewer_client_id: int, room_id: int) -> None: ...


def build_chat_api(
    db: AsyncSession,
    redis: Redis,
    *,
    event_bus: Optional[EventBus] = None,
) -> ChatAPI:
    from app.fittbot_api.v2.Fymble.gym_mate.blocks._repository import (
        BlockRepository,
    )
    from ._pubsub import ChatPublisher
    from ._repository import (
        ChatMessageRepository,
        ChatParticipantRepository,
        ChatPolicyRepository,
        ChatRoomRepository,
    )
    from ._service import ChatService

    return ChatService(
        rooms=ChatRoomRepository(db),
        participants=ChatParticipantRepository(db),
        messages=ChatMessageRepository(db),
        policy=ChatPolicyRepository(db),
        publisher=ChatPublisher(redis),
        event_bus=event_bus or NoopEventBus(),
        blocks=BlockRepository(db),
    )
