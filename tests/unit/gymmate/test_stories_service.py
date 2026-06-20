from datetime import datetime
from typing import Optional

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.stories import _domain as d
from app.fittbot_api.v2.Fymble.gym_mate.stories._events import (
    StoryDeleted,
    StoryPublished,
)
from app.fittbot_api.v2.Fymble.gym_mate.stories._service import StoryService
from app.fittbot_api.v2.Fymble.gym_mate.stories._storage import (
    PresignedStoryUpload,
    StoryMediaStorage,
)
from app.utils.logging_utils import FittbotHTTPException


class InMemoryStoryRepository:
    def __init__(self):
        self.rows: dict[int, d.Story] = {}
        self._next = 1

    async def add(self, story):
        story.id = self._next
        self._next += 1
        self.rows[story.id] = story
        return story

    async def get_by_id(self, story_id):
        return self.rows.get(story_id)

    async def mark_deleted(self, story_id, deleted_at):
        s = self.rows.get(story_id)
        if s is not None:
            s.is_deleted = True
            s.deleted_at = deleted_at


class RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class StubStorage(StoryMediaStorage):
    def presign_upload(self, client_id, content_type):
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise FittbotHTTPException(
                status_code=400, detail="bad",
                error_code="GYMMATE_STORY_BAD_CONTENT_TYPE",
                log_data={},
            )
        key = f"gym_mate/stories/{client_id}/stub.jpg"
        return PresignedStoryUpload(
            url="https://stub.s3/", fields={"Content-Type": content_type},
            key=key, cdn_url=f"https://stub.s3/{key}?v=1", version=1,
        )


@pytest.fixture
def repo(): return InMemoryStoryRepository()

@pytest.fixture
def bus(): return RecordingBus()

@pytest.fixture
def service(repo, bus):
    return StoryService(repository=repo, event_bus=bus, storage=StubStorage())


def _key(client_id, name="x.jpg"):
    return f"gym_mate/stories/{client_id}/{name}"


class TestPresignMedia:
    @pytest.mark.asyncio
    async def test_returns_envelope(self, service):
        env = await service.presign_media(42, "image/jpeg")
        assert env.key.startswith("gym_mate/stories/42/")
        assert env.url.startswith("https://")

    @pytest.mark.asyncio
    async def test_bad_content_type_rejected(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.presign_media(42, "image/heic")
        assert exc.value.error_code == "GYMMATE_STORY_BAD_CONTENT_TYPE"


class TestCreateStory:
    @pytest.mark.asyncio
    async def test_creates_story_and_publishes_event(self, service, repo, bus):
        result = await service.create_story(
            client_id=42, s3_key=_key(42),
        )
        assert result.story_id is not None
        assert result.client_id == 42
        assert result.audience == "public"
        assert result.media_type == "image"

        stored = await repo.get_by_id(result.story_id)
        assert stored is not None
        assert stored.is_deleted is False

        assert len(bus.events) == 1
        assert isinstance(bus.events[0], StoryPublished)
        assert bus.events[0].client_id == 42

    @pytest.mark.asyncio
    async def test_with_caption_and_friends_audience(self, service):
        result = await service.create_story(
            client_id=42, s3_key=_key(42),
            caption="testing", audience="friends",
        )
        assert result.caption == "testing"
        assert result.audience == "friends"

    @pytest.mark.asyncio
    async def test_rejects_foreign_key(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.create_story(client_id=42, s3_key=_key(99))
        assert exc.value.error_code == "GYMMATE_STORY_FOREIGN_KEY"

    @pytest.mark.asyncio
    async def test_rejects_foreign_thumbnail(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.create_story(
                client_id=42, s3_key=_key(42),
                thumbnail_key=_key(99, "thumb.jpg"),
            )
        assert exc.value.error_code == "GYMMATE_STORY_FOREIGN_KEY"

    @pytest.mark.asyncio
    async def test_invalid_audience_rejected(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.create_story(
                client_id=42, s3_key=_key(42), audience="strangers",
            )
        assert exc.value.error_code == "GYMMATE_STORY_INVALID"

    @pytest.mark.asyncio
    async def test_caption_over_limit_rejected(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.create_story(
                client_id=42, s3_key=_key(42), caption="x" * 301,
            )
        assert exc.value.error_code == "GYMMATE_STORY_INVALID"

    @pytest.mark.asyncio
    async def test_expires_at_is_24h_ahead(self, service):
        before = datetime.now()
        result = await service.create_story(client_id=42, s3_key=_key(42))
        diff = (result.expires_at - result.created_at).total_seconds()
        assert 23.9 * 3600 <= diff <= 24.1 * 3600


class TestDeleteStory:
    @pytest.mark.asyncio
    async def test_owner_can_delete(self, service, repo, bus):
        created = await service.create_story(client_id=42, s3_key=_key(42))
        bus.events.clear()

        await service.delete_story(client_id=42, story_id=created.story_id)

        stored = await repo.get_by_id(created.story_id)
        assert stored.is_deleted is True
        assert any(isinstance(e, StoryDeleted) for e in bus.events)

    @pytest.mark.asyncio
    async def test_non_owner_forbidden(self, service):
        created = await service.create_story(client_id=42, s3_key=_key(42))
        with pytest.raises(FittbotHTTPException) as exc:
            await service.delete_story(client_id=99, story_id=created.story_id)
        assert exc.value.status_code == 403
        assert exc.value.error_code == "GYMMATE_STORY_FORBIDDEN"

    @pytest.mark.asyncio
    async def test_not_found(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.delete_story(client_id=42, story_id=99999)
        assert exc.value.status_code == 404
        assert exc.value.error_code == "GYMMATE_STORY_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_double_delete_rejected(self, service):
        created = await service.create_story(client_id=42, s3_key=_key(42))
        await service.delete_story(client_id=42, story_id=created.story_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.delete_story(client_id=42, story_id=created.story_id)
        assert exc.value.error_code == "GYMMATE_STORY_ALREADY_DELETED"
