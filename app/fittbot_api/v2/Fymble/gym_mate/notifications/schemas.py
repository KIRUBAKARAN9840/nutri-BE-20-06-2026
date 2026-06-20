"""DTOs returned by the notifications module."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class NotificationActorDTO(BaseModel):
    """Who triggered the notification — used to render the avatar
    on the row. Null for system-generated notifications."""
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class NotificationDTO(BaseModel):
    """One row in the notification center feed.

    `payload.data` carries the destination route the frontend's tap
    handler switches on (e.g. "friends", "my_requests", "chat_thread").
    `payload.params` carries the route arguments. Same shape ships in
    the FCM push data so one tap handler covers both surfaces.
    """
    id: int
    category: str
    title: str
    body: Optional[str] = None
    actor: Optional[NotificationActorDTO] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    payload: Dict[str, Any] = {}
    read_at: Optional[datetime] = None
    created_at: datetime


class NotificationPageDTO(BaseModel):
    """Cursor-paginated feed. `unread_count` is included on the first
    page only (when no cursor is supplied) so the frontend can paint the
    bell badge without a separate roundtrip."""
    items: List[NotificationDTO]
    next_cursor: Optional[datetime] = None
    has_more: bool = False
    unread_count: Optional[int] = None


class UnreadCountDTO(BaseModel):
    count: int


class DeviceTokenDTO(BaseModel):
    """Confirmation of a registered token."""
    token: str
    platform: str


# ── Home-page summary (per-bucket unread counts) ─────────────────────────────

class HomeNotificationDotDTO(BaseModel):
    """Single-type bucket — just a dot flag. Used for chat where every
    notification in the bucket goes to the same tab."""
    has_unread: bool = False


class HomeFriendRequestsDotDTO(BaseModel):
    """Friend-requests bucket. Same logic as HomeNotificationDotDTO but
    also surfaces the count so the frontend can render a numeric badge
    next to the dot. `count` is the number of unread notifications in
    the same category set that drives `has_unread` — so the two are
    consistent: `has_unread == (count > 0)`.

    `recent_avatars` carries up to 3 full CDN URLs of the most-recent
    pending friend-request SENDERS (i.e. people waiting on the user to
    accept). Same avatar precedence as everywhere else: gym_mate primary
    photo → clients.profile → dropped if both null. Populated by the
    gym_mate home service which has access to the friends API.
    """
    has_unread: bool = False
    count: int = 0
    recent_avatars: List[str] = []


class HomeGymMateConnectionsDTO(BaseModel):
    """Two-type bucket — gym mate connections covers two distinct
    destinations on the frontend:

      `has_received` = True  → session join requests received (route to
                               My Requests → Received tab)
      `has_match`    = True  → session_request_accepted / session_new_match
                               / session_cancelled_by_host
                               (route to Matches tab)

    Both can be true at once (e.g. someone wants to join your session
    AND a different session just got a new joiner). Frontend can paint
    a single dot whenever either flag is true, then look at the flags
    when the user taps to decide which tab to land on.
    """
    has_received: bool = False
    has_match: bool = False


class HomeNotificationsSummaryDTO(BaseModel):
    """The three home-page dots.

      gym_mate_connections — split into received vs match so the tap
                             handler knows which tab to open
      friend_requests      — friend_request_received + friend_request_accepted
      chat                 — chat_message_direct + chat_message_group
    """
    gym_mate_connections: HomeGymMateConnectionsDTO = HomeGymMateConnectionsDTO()
    friend_requests: HomeFriendRequestsDotDTO = HomeFriendRequestsDotDTO()
    chat: HomeNotificationDotDTO = HomeNotificationDotDTO()
