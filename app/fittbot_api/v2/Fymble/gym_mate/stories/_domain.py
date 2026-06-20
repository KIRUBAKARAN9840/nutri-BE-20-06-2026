

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from app.utils.time_utils import utc_now


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class StoryMediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class StoryAudience(str, Enum):
    PUBLIC = "public"
    FRIENDS = "friends"


# ---------------------------------------------------------------------------
# Value Objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StoryCaption:
    value: str
    MAX_LEN = 300

    def __post_init__(self) -> None:
        trimmed = self.value.strip()
        if len(trimmed) > self.MAX_LEN:
            raise InvalidCaption(f"caption max {self.MAX_LEN} chars")
        object.__setattr__(self, "value", trimmed)


@dataclass(frozen=True)
class S3MediaKey:
    """An S3 key for story media. Validated to live under the per-user
    stories prefix at the service layer (needs client_id)."""
    value: str
    REQUIRED_PREFIX = "gym_mate/stories/"
    MAX_LEN = 500

    def __post_init__(self) -> None:
        if not self.value:
            raise InvalidStoryMediaKey("media key is empty")
        if len(self.value) > self.MAX_LEN:
            raise InvalidStoryMediaKey(f"media key exceeds {self.MAX_LEN} chars")
        if not self.value.startswith(self.REQUIRED_PREFIX):
            raise InvalidStoryMediaKey(
                f"media key must start with '{self.REQUIRED_PREFIX}'"
            )


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------
class StoryDomainError(Exception):
    """Base class for story-related business-rule violations."""


class InvalidCaption(StoryDomainError): ...
class InvalidStoryMediaKey(StoryDomainError): ...
class StoryNotOwned(StoryDomainError): ...
class StoryAlreadyDeleted(StoryDomainError): ...
class StoryExpired(StoryDomainError): ...


# ---------------------------------------------------------------------------
# Story aggregate
# ---------------------------------------------------------------------------
TTL_HOURS = 24


@dataclass
class Story:
    client_id: int
    media_type: StoryMediaType
    s3_key: S3MediaKey
    audience: StoryAudience
    created_at: datetime
    expires_at: datetime

    thumbnail_key: Optional[S3MediaKey] = None
    caption: Optional[StoryCaption] = None
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
    id: Optional[int] = None

    @classmethod
    def publish(
        cls,
        client_id: int,
        s3_key: S3MediaKey,
        media_type: StoryMediaType = StoryMediaType.IMAGE,
        audience: StoryAudience = StoryAudience.PUBLIC,
        caption: Optional[StoryCaption] = None,
        thumbnail_key: Optional[S3MediaKey] = None,
        now: Optional[datetime] = None,
    ) -> "Story":
        """Factory — creates a Story with the 24h TTL applied."""
        created = now or utc_now()
        return cls(
            client_id=client_id,
            media_type=media_type,
            s3_key=s3_key,
            audience=audience,
            caption=caption,
            thumbnail_key=thumbnail_key,
            created_at=created,
            expires_at=created + timedelta(hours=TTL_HOURS),
        )

    def delete_by(self, requester_client_id: int, now: Optional[datetime] = None) -> None:

        if requester_client_id != self.client_id:
            raise StoryNotOwned("Only the owner can delete this story")
        if self.is_deleted:
            raise StoryAlreadyDeleted("Story already deleted")
        self.is_deleted = True
        self.deleted_at = now or utc_now()

    def is_active(self, now: Optional[datetime] = None) -> bool:
       
        moment = now or utc_now()
        return (not self.is_deleted) and self.expires_at > moment


