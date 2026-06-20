from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class PresignedUploadEnvelopeDTO(BaseModel):
    url: str
    fields: dict


class PresignedStoryUploadDTO(BaseModel):
    upload: PresignedUploadEnvelopeDTO
    cdn_url: str
    version: int


class StoryDTO(BaseModel):
    story_id: int
    client_id: int
    media_type: str
    s3_key: str
    cdn_url: str
    thumbnail_url: Optional[str] = None
    caption: Optional[str] = None
    audience: str
    created_at: datetime
    expires_at: datetime


class MyStorySummaryDTO(BaseModel):
    # Same identity + ring fields as CarouselAuthorDTO, so the frontend
    # can render this slot using the exact same circle component.
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    all_viewed: bool = False
    story_count: int = 0
    latest_at: Optional[datetime] = None
    is_friend: bool = False

    # My-only fields:
    has_active: bool = False
    story_id: Optional[int] = None
    expires_at: Optional[datetime] = None
    view_count: int = 0


class CarouselAuthorDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    all_viewed: bool
    story_count: int
    latest_at: datetime
    is_friend: bool


class StoryViewerAuthorDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class StoryViewerItemDTO(BaseModel):
    story_id: int
    media_type: str
    cdn_url: str
    thumbnail_url: Optional[str] = None
    caption: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    is_viewed: bool


class StoriesForClientDTO(BaseModel):
    client: StoryViewerAuthorDTO
    stories: List[StoryViewerItemDTO]
