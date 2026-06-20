"""Notification domain — categories, screens, error types.

The notification record itself is a value object (no business rules
beyond category validation). All the orchestration — coalesce, throttle,
push fan-out — lives in the service layer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class NotificationCategory(str, Enum):
    # Friends
    FRIEND_REQUEST_RECEIVED = "friend_request_received"
    FRIEND_REQUEST_ACCEPTED = "friend_request_accepted"
    # Sessions
    SESSION_REQUEST_RECEIVED = "session_request_received"
    SESSION_REQUEST_ACCEPTED = "session_request_accepted"
    SESSION_NEW_MATCH = "session_new_match"
    SESSION_CANCELLED_BY_HOST = "session_cancelled_by_host"
    # Chat
    CHAT_MESSAGE_DIRECT = "chat_message_direct"
    CHAT_MESSAGE_GROUP = "chat_message_group"
    # Stories
    STORY_FROM_FRIEND = "story_from_friend"


class NotificationTarget(str, Enum):
    """Frontend route identifiers — the navigator's tap handler
    dispatches on `payload.data`. Keep these stable; renaming one is a
    breaking change for any unread notification still sitting in users'
    bell-icon feeds (the route string was stored at insert-time)."""
    FRIENDS = "friends"
    MY_REQUESTS = "my_requests"
    MATCHES = "matches"
    RECEIVED = "received"
    CHAT_THREAD = "chat_thread"
    HOME = "home"
    STORIES = "stories"


# Backward-compat alias — older code may still import NotificationScreen.
NotificationScreen = NotificationTarget


class NotificationDomainError(Exception):
    pass


class InvalidCategory(NotificationDomainError):
    pass


class InvalidPlatform(NotificationDomainError):
    pass


VALID_PLATFORMS = ("ios", "android", "web")


@dataclass
class Notification:
    """Value object for a notification row. Carries enough for the
    handler to INSERT + the FCM task to build a push payload."""
    recipient_client_id: int
    category: NotificationCategory
    title: str
    body: Optional[str] = None
    actor_client_id: Optional[int] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


def validate_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p not in VALID_PLATFORMS:
        raise InvalidPlatform(
            f"Platform must be one of {VALID_PLATFORMS}, got '{platform}'"
        )
    return p
