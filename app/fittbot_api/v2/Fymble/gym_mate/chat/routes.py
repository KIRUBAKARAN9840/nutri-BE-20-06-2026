from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from ._http_schemas import (
    BulkDeleteFailureDTO,
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkDeleteResultDTO,
    EditMessageRequest,
    EmptyResponse,
    InboxResponse,
    MarkReadRequest,
    MessageHistoryResponse,
    MessageResponse,
    OpenDirectRoomRequest,
    ReportRoomRequest,
    RoomResponse,
    SendMessageRequest,
)
from .api import ChatAPI, build_chat_api


router = APIRouter(prefix="/gym_mate/chat", tags=["GymMate Chat V2"])



CHAT_VIEWING_TTL_SECONDS = 300


def chat_viewing_key(client_id: int) -> str:
    return f"gymmate:chat:viewing:{client_id}"


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> ChatAPI:

    try:
        from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
            gymmate_event_bus,
        )
        return build_chat_api(db, redis, event_bus=gymmate_event_bus)
    except ImportError:
        return build_chat_api(db, redis)


@router.get("/rooms", response_model=InboxResponse)
@log_exceptions
async def list_inbox(
    request: Request,
    before_at: Optional[datetime] = Query(
        None,
        description="Cursor: pass `next_cursor` from the previous page",
    ),
    limit: int = Query(30, ge=1, le=50),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    page = await api.list_inbox(
        client_id=client_id, before_at=before_at, limit=limit,
    )
    return InboxResponse(data=page)


@router.post("/rooms/direct", response_model=RoomResponse)
@log_exceptions
async def open_direct_room(
    req: OpenDirectRoomRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    data = await api.open_direct_room(
        viewer_client_id=client_id,
        peer_client_id=req.peer_client_id,
        session_id=req.session_id,
    )
    await db.commit()
    return RoomResponse(data=data)


@router.get("/rooms/session/{session_id}", response_model=RoomResponse)
@log_exceptions
async def open_session_group_room(
    session_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    data = await api.open_session_group_room(
        viewer_client_id=client_id, session_id=session_id,
    )
    await db.commit()
    return RoomResponse(data=data)



@router.get("/rooms/{room_id}", response_model=RoomResponse)
@log_exceptions
async def get_room(
    room_id: int,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    data = await api.get_room(viewer_client_id=client_id, room_id=room_id)
    return RoomResponse(data=data)


@router.get(
    "/rooms/{room_id}/messages", response_model=MessageHistoryResponse,
)
@log_exceptions
async def list_history(
    room_id: int,
    request: Request,
    before: Optional[int] = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    rows = await api.list_history(
        viewer_client_id=client_id,
        room_id=room_id,
        before=before,
        limit=limit,
    )
    peer_id = await api.peer_of_room(
        room_id=room_id, viewer_client_id=client_id,
    )
    return MessageHistoryResponse(data=rows, peer_client_id=peer_id)


@router.post("/rooms/{room_id}/messages", response_model=MessageResponse)
@log_exceptions
async def send_message(
    room_id: int,
    req: SendMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    data = await api.send_message(
        sender_client_id=client_id,
        room_id=room_id,
        body=req.body,
        client_msg_id=req.client_msg_id,
    )
    peer_id = await api.peer_of_room(
        room_id=room_id, viewer_client_id=client_id,
    )
    await db.commit()
    return MessageResponse(data=data, peer_client_id=peer_id)


@router.post("/rooms/{room_id}/read", response_model=EmptyResponse)
@log_exceptions
async def mark_read(
    room_id: int,
    req: MarkReadRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    await api.mark_read(
        viewer_client_id=client_id,
        room_id=room_id,
        up_to_message_id=req.up_to_message_id,
    )
    await db.commit()
    # Mark this client as actively viewing this room. The chat
    # notification handler reads this to skip the push when the
    # recipient is already inside the thread the message lands in.
    try:
        await redis.set(
            chat_viewing_key(client_id),
            str(room_id),
            ex=CHAT_VIEWING_TTL_SECONDS,
        )
    except Exception:
        # Non-fatal — failure means we'll over-push, never miss.
        pass
    return EmptyResponse()


@router.patch("/messages/{message_id}", response_model=MessageResponse)
@log_exceptions
async def edit_message(
    message_id: int,
    req: EditMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    data = await api.edit_message(
        sender_client_id=client_id, message_id=message_id, body=req.body,
    )
    peer_id = await api.peer_of_room(
        room_id=data.room_id, viewer_client_id=client_id,
    )
    await db.commit()
    return MessageResponse(data=data, peer_client_id=peer_id)


@router.post("/messages/delete", response_model=BulkDeleteResponse)
@log_exceptions
async def delete_messages(
    body: BulkDeleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):

    result = await api.delete_messages_bulk(
        sender_client_id=client_id, message_ids=body.message_ids,
    )
    await db.commit()
    return BulkDeleteResponse(
        data=BulkDeleteResultDTO(
            deleted=result["deleted"],
            failed=[BulkDeleteFailureDTO(**f) for f in result["failed"]],
        )
    )


@router.post("/rooms/{room_id}/leave", response_model=EmptyResponse)
@log_exceptions
async def leave_room(
    room_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    """Leave a session_group chat. Per product call, this also removes
    the user from the session itself — the chat is the coordination
    layer, opting out of the chat = opting out of the workout.
    Refused for the session host; they must cancel the session instead."""
    await api.leave_session_group(
        viewer_client_id=client_id, room_id=room_id,
    )
    await db.commit()
    return EmptyResponse()


@router.post("/rooms/{room_id}/report", response_model=EmptyResponse)
@log_exceptions
async def report_room(
    room_id: int,
    body: ReportRoomRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ChatAPI = Depends(_api),
):
    """Report a chat room (the whole conversation) to the moderation
    queue. Idempotent — duplicate reports from the same user on the
    same room silently no-op."""
    await api.report_room(
        reporter_client_id=client_id,
        room_id=room_id,
        reason=body.reason,
        details=body.details,
    )
    await db.commit()
    return EmptyResponse()
