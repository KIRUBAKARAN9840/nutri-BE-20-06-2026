from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel


class MessageDTO(BaseModel):
    message_id: int
    room_id: int
    sender_client_id: int
    body: str
    kind: str
    client_msg_id: Optional[str] = None
    created_at: datetime
    edited_at: Optional[datetime] = None
    is_deleted: bool = False


class LastMessagePreviewDTO(BaseModel):
    message_id: int
    sender_client_id: int
    body: str
    kind: str
    created_at: datetime
    is_deleted: bool = False


class InboxPeerDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class InboxGroupDTO(BaseModel):
    session_id: int
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    gym_area: Optional[str] = None
    gym_cover_pic: Optional[str] = None
    session_date: Optional[str] = None
    session_time: Optional[str] = None
    member_count: int = 0
    member_avatars: List[str] = []   


class InboxItemDTO(BaseModel):
    room_id: int
    kind: str
    session_id: Optional[int] = None
    title: str
    avatar_url: Optional[str] = None
    subtitle: Optional[str] = None
    last_message_at: Optional[datetime] = None
    last_message: Optional[LastMessagePreviewDTO] = None
    unread_count: int = 0
    peer: Optional[InboxPeerDTO] = None
    group: Optional[InboxGroupDTO] = None


class RecentFriendDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    friended_at: Optional[datetime] = None


class InboxPageDTO(BaseModel):
    items: List[InboxItemDTO]
    next_cursor: Optional[datetime] = None
    has_more: bool = False
    recent_friends: List[RecentFriendDTO] = []


class ParticipantDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    joined_at: Optional[datetime] = None


class RoomDTO(BaseModel):
    room_id: int
    kind: str
    session_id: Optional[int] = None
    pair_key: Optional[str] = None
    participants: List[ParticipantDTO] = []
    last_message_at: Optional[datetime] = None
    gym_name: Optional[str] = None  
    gym_cover_pic: Optional[str] = None
    session_date: Optional[date] = None
    session_time: Optional[time] = None
    peer_client_id: Optional[int] = None

