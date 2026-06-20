from typing import Callable, List, Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import (
    CarouselAuthorDTO,
    MyStorySummaryDTO,
    StoriesForClientDTO,
    StoryDTO,
)
from ._events import EventBus, NoopEventBus
from ._storage import PresignedStoryUpload


class StoriesAPI(Protocol):

    async def presign_media(
        self,
        client_id: int,
        content_type: str,
    ) -> PresignedStoryUpload: ...

    async def create_story(
        self,
        client_id: int,
        s3_key: str,
        media_type: str = "image",
        caption: Optional[str] = None,
        audience: str = "public",
        thumbnail_key: Optional[str] = None,
    ) -> StoryDTO: ...

    async def delete_story(self, client_id: int, story_id: int) -> None: ...

    async def get_my_story_summary(self, client_id: int) -> MyStorySummaryDTO: ...

    async def get_home_carousel(
        self, viewer_id: int, limit: int = 20
    ) -> List[CarouselAuthorDTO]: ...

    async def get_stories_for_client(
        self, viewer_id: int, author_id: int
    ) -> StoriesForClientDTO: ...

    async def record_view(self, viewer_id: int, story_id: int) -> None: ...


def build_stories_api(
    db: AsyncSession,
    redis: Optional[Redis] = None,
    *,
    event_bus: Optional[EventBus] = None,
    on_owner_change: Optional[Callable] = None,
) -> StoriesAPI:
    from ._repository import StoryRepository
    from ._service import StoryService
    from ._storage import StoryMediaStorage

    return StoryService(
        repository=StoryRepository(db),
        event_bus=event_bus or NoopEventBus(),
        storage=StoryMediaStorage(),
        on_owner_change=on_owner_change,
    )
