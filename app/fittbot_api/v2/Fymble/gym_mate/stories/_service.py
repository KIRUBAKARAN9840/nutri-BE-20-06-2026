from datetime import datetime
from typing import List, Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._events import EventBus, StoryDeleted, StoryPublished
from ._repository import StoryRepository
from ._storage import (
    PresignedStoryUpload,
    StoryMediaStorage,
    build_cdn_url,
)


def _avatar_url_or_none(value: Optional[str]) -> Optional[str]:
    """clients.profile can be a full URL already or a bare S3 key.
    Return as-is if it looks like a URL, otherwise build one."""
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return build_cdn_url(value)


class StoryService:
    def __init__(
        self,
        repository: StoryRepository,
        event_bus: EventBus,
        storage: StoryMediaStorage,
        on_owner_change=None,
    ):
        self.repo = repository
        self.bus = event_bus
        self.storage = storage
        # Optional callback: invalidates the owner's home cache. Used by
        # the home module so that posting/deleting/viewing a story
        # immediately refreshes the carousel for the actor.
        self._on_owner_change = on_owner_change

    async def _bust_owner(self, client_id: int) -> None:
        if self._on_owner_change is None:
            return
        try:
            await self._on_owner_change(client_id)
        except Exception:
            pass

    async def presign_media(
        self,
        client_id: int,
        content_type: str,
    ) -> PresignedStoryUpload:
        return self.storage.presign_upload(client_id, content_type)

    async def create_story(
        self,
        client_id: int,
        s3_key: str,
        media_type: str = "image",
        caption: Optional[str] = None,
        audience: str = "public",
        thumbnail_key: Optional[str] = None,
    ) -> dto.StoryDTO:
        expected_prefix = StoryMediaStorage.expected_prefix_for(client_id)
        if not s3_key.startswith(expected_prefix):
            raise FittbotHTTPException(
                status_code=400,
                detail="Media key does not belong to this user",
                error_code="GYMMATE_STORY_FOREIGN_KEY",
                log_data={"client_id": client_id, "key": s3_key},
            )
        if thumbnail_key and not thumbnail_key.startswith(expected_prefix):
            raise FittbotHTTPException(
                status_code=400,
                detail="Thumbnail key does not belong to this user",
                error_code="GYMMATE_STORY_FOREIGN_KEY",
                log_data={"client_id": client_id, "thumbnail_key": thumbnail_key},
            )

        try:
            story = d.Story.publish(
                client_id=client_id,
                s3_key=d.S3MediaKey(s3_key),
                media_type=d.StoryMediaType(media_type),
                audience=d.StoryAudience(audience),
                caption=d.StoryCaption(caption) if caption else None,
                thumbnail_key=d.S3MediaKey(thumbnail_key) if thumbnail_key else None,
            )
        except (ValueError, d.StoryDomainError) as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_STORY_INVALID",
                log_data={"client_id": client_id, "exc": repr(exc)},
            )

        await self.repo.add(story)

        await self.bus.publish(StoryPublished(
            story_id=story.id,
            client_id=client_id,
            audience=story.audience.value,
            expires_at=story.expires_at,
        ))

        await self._bust_owner(client_id)

        return self._to_dto(story)

    async def delete_story(self, client_id: int, story_id: int) -> None:
        story = await self.repo.get_by_id(story_id)
        if story is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Story not found",
                error_code="GYMMATE_STORY_NOT_FOUND",
                log_data={"client_id": client_id, "story_id": story_id},
            )

        try:
            story.delete_by(client_id)
        except d.StoryNotOwned as exc:
            raise FittbotHTTPException(
                status_code=403,
                detail=str(exc),
                error_code="GYMMATE_STORY_FORBIDDEN",
                log_data={"client_id": client_id, "story_id": story_id},
            )
        except d.StoryAlreadyDeleted as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_STORY_ALREADY_DELETED",
                log_data={"client_id": client_id, "story_id": story_id},
            )

        await self.repo.mark_deleted(story_id, story.deleted_at)
        await self.bus.publish(StoryDeleted(
            story_id=story_id,
            client_id=client_id,
        ))
        await self._bust_owner(client_id)

    async def get_my_story_summary(self, client_id: int) -> dto.MyStorySummaryDTO:
        row = await self.repo.get_my_active_summary(client_id)
        return dto.MyStorySummaryDTO(
            client_id=row["client_id"],
            name=row["name"],
            avatar_url=_avatar_url_or_none(row["avatar_url"]),
            has_active=row["has_active"],
            # ring is green when I have an active story (treat as "unviewed"
            # from the carousel's color logic so frontend uses one rule)
            all_viewed=False if row["has_active"] else True,
            story_count=row["story_count"],
            latest_at=row["latest_at"],
            is_friend=False,
            story_id=row["story_id"],
            expires_at=row["expires_at"],
            view_count=row["view_count"],
        )

    async def get_home_carousel(
        self, viewer_id: int, limit: int = 20
    ) -> List[dto.CarouselAuthorDTO]:
        rows = await self.repo.get_home_carousel(viewer_id, limit=limit)
        return [
            dto.CarouselAuthorDTO(
                client_id=r["author_id"],
                name=r["author_name"],
                avatar_url=_avatar_url_or_none(r["author_avatar"]),
                all_viewed=bool(int(r["all_viewed"])),
                story_count=int(r["story_count"]),
                latest_at=r["latest_at"],
                is_friend=bool(int(r["is_friend"])),
            )
            for r in rows
        ]

    async def get_stories_for_client(
        self, viewer_id: int, author_id: int
    ) -> dto.StoriesForClientDTO:
        row = await self.repo.get_active_stories_for_client(viewer_id, author_id)
        if row is None or not row["stories"]:
            raise FittbotHTTPException(
                status_code=404,
                detail="No active stories for this client",
                error_code="GYMMATE_STORY_NONE_ACTIVE",
                log_data={"viewer_id": viewer_id, "author_id": author_id},
            )
        return dto.StoriesForClientDTO(
            client=dto.StoryViewerAuthorDTO(
                client_id=row["author_id"],
                name=row["author_name"],
                avatar_url=_avatar_url_or_none(row["author_avatar"]),
            ),
            stories=[
                dto.StoryViewerItemDTO(
                    story_id=s["story_id"],
                    media_type=s["media_type"],
                    cdn_url=build_cdn_url(s["s3_key"]),
                    thumbnail_url=build_cdn_url(s["thumbnail_key"]) if s["thumbnail_key"] else None,
                    caption=s["caption"],
                    created_at=s["created_at"],
                    expires_at=s["expires_at"],
                    is_viewed=s["is_viewed"],
                )
                for s in row["stories"]
            ],
        )

    async def record_view(self, viewer_id: int, story_id: int) -> None:
        recorded = await self.repo.record_view(viewer_id, story_id)
        if not recorded:
            raise FittbotHTTPException(
                status_code=404,
                detail="Story not visible to viewer",
                error_code="GYMMATE_STORY_VIEW_FORBIDDEN",
                log_data={"viewer_id": viewer_id, "story_id": story_id},
            )
        await self._bust_owner(viewer_id)

    @staticmethod
    def _to_dto(story: d.Story) -> dto.StoryDTO:
        return dto.StoryDTO(
            story_id=story.id,
            client_id=story.client_id,
            media_type=story.media_type.value,
            s3_key=story.s3_key.value,
            cdn_url=build_cdn_url(story.s3_key.value),
            thumbnail_url=(
                build_cdn_url(story.thumbnail_key.value)
                if story.thumbnail_key else None
            ),
            caption=story.caption.value if story.caption else None,
            audience=story.audience.value,
            created_at=story.created_at,
            expires_at=story.expires_at,
        )
