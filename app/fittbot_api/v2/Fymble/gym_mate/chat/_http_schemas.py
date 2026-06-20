from typing import List, Optional

from pydantic import BaseModel, Field

from .schemas import InboxItemDTO, InboxPageDTO, MessageDTO, RoomDTO


class OpenDirectRoomRequest(BaseModel):
    peer_client_id: int
    session_id: Optional[int] = None


class SendMessageRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    client_msg_id: Optional[str] = Field(None, max_length=36)


class EditMessageRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class MarkReadRequest(BaseModel):
    up_to_message_id: int


class BulkDeleteRequest(BaseModel):
    # Cap matches the max history page size — discourages giant abusive batches.
    message_ids: List[int] = Field(..., min_length=1, max_length=100)


class BulkDeleteFailureDTO(BaseModel):
    message_id: int
    error_code: str
    detail: str


class BulkDeleteResultDTO(BaseModel):
    deleted: List[int] = []
    failed: List[BulkDeleteFailureDTO] = []


class BulkDeleteResponse(BaseModel):
    status: int = 200
    data: BulkDeleteResultDTO


class ReportRoomRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=40)
    details: Optional[str] = Field(None, max_length=500)


class RoomResponse(BaseModel):
    status: int = 200
    data: RoomDTO


class InboxResponse(BaseModel):
    status: int = 200
    data: InboxPageDTO


class MessageResponse(BaseModel):
    status: int = 200
    data: MessageDTO
    # The OTHER party (relative to the JWT viewer) when the message is
    # in a 1:1 room. Null for session_group rooms.
    peer_client_id: Optional[int] = None


class MessageHistoryResponse(BaseModel):
    status: int = 200
    data: List[MessageDTO]
    # One value for the whole page — peer doesn't change within a room.
    peer_client_id: Optional[int] = None


class EmptyResponse(BaseModel):
    status: int = 200
    message: str = "ok"
