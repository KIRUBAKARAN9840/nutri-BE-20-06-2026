from datetime import datetime
from typing import Optional

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.stories._events import NoopEventBus
from app.fittbot_api.v2.Fymble.gym_mate.stories._service import StoryService
from app.fittbot_api.v2.Fymble.gym_mate.stories._storage import StoryMediaStorage
from app.utils.logging_utils import FittbotHTTPException


def _empty_my_summary(client_id, name=None, avatar=None):
    return {
        "client_id": client_id, "name": name, "avatar_url": avatar,
        "has_active": False, "story_id": None, "story_count": 0,
        "view_count": 0, "expires_at": None, "latest_at": None,
    }


class FakeRepo:
    """Stand-in for the new home/viewer/view methods on StoryRepository."""

    def __init__(self):
        self.my_summary = _empty_my_summary(42)
        self.carousel_rows = []
        self.by_client = None
        self.record_view_returns = True
        self.recorded = []

    async def get_my_active_summary(self, client_id):
        # If test set self.my_summary, return it as-is. Else default empty.
        return self.my_summary if self.my_summary is not None else _empty_my_summary(client_id)

    async def get_home_carousel(self, viewer_id, limit=20):
        return self.carousel_rows[:limit]

    async def get_active_stories_for_client(self, viewer_id, author_id):
        return self.by_client

    async def record_view(self, viewer_id, story_id):
        self.recorded.append((viewer_id, story_id))
        return self.record_view_returns


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def service(repo):
    return StoryService(
        repository=repo,
        event_bus=NoopEventBus(),
        storage=StoryMediaStorage(),
    )


class TestMyStorySummary:
    @pytest.mark.asyncio
    async def test_no_active_story(self, service, repo):
        repo.my_summary = _empty_my_summary(42, name="Raj", avatar="https://x/r.jpg")
        result = await service.get_my_story_summary(42)
        assert result.client_id == 42
        assert result.name == "Raj"
        assert result.avatar_url == "https://x/r.jpg"
        assert result.has_active is False
        assert result.all_viewed is True
        assert result.story_id is None
        assert result.story_count == 0
        assert result.view_count == 0

    @pytest.mark.asyncio
    async def test_active_story(self, service, repo):
        repo.my_summary = {
            "client_id": 42,
            "name": "Raj",
            "avatar_url": "https://x/r.jpg",
            "has_active": True,
            "story_id": 12,
            "story_count": 2,
            "view_count": 7,
            "expires_at": datetime(2026, 5, 26, 10, 0),
            "latest_at": datetime(2026, 5, 25, 10, 0),
        }
        result = await service.get_my_story_summary(42)
        assert result.client_id == 42
        assert result.name == "Raj"
        assert result.has_active is True
        assert result.all_viewed is False
        assert result.story_id == 12
        assert result.story_count == 2
        assert result.view_count == 7
        assert result.latest_at == datetime(2026, 5, 25, 10, 0)
        assert result.is_friend is False


class TestHomeCarousel:
    @pytest.mark.asyncio
    async def test_empty(self, service):
        result = await service.get_home_carousel(42)
        assert result == []

    @pytest.mark.asyncio
    async def test_maps_rows(self, service, repo):
        repo.carousel_rows = [
            {
                "author_id": 88, "author_name": "Selena",
                "author_avatar": "https://cdn/selena.jpg",
                "all_viewed": 0, "story_count": 2,
                "latest_at": datetime(2026, 5, 25, 18, 0),
                "is_friend": 1,
            },
            {
                "author_id": 99, "author_name": "Random Guy",
                "author_avatar": None,
                "all_viewed": 1, "story_count": 1,
                "latest_at": datetime(2026, 5, 25, 11, 0),
                "is_friend": 0,
            },
        ]
        result = await service.get_home_carousel(42)
        assert len(result) == 2
        assert result[0].name == "Selena"
        assert result[0].all_viewed is False
        assert result[0].is_friend is True
        assert result[1].avatar_url is None
        assert result[1].all_viewed is True

    @pytest.mark.asyncio
    async def test_avatar_url_is_passed_through_if_full_url(self, service, repo):
        repo.carousel_rows = [{
            "author_id": 88, "author_name": "X",
            "author_avatar": "https://something/x.jpg",
            "all_viewed": 0, "story_count": 1,
            "latest_at": datetime(2026, 5, 25, 10, 0), "is_friend": 1,
        }]
        result = await service.get_home_carousel(42)
        assert result[0].avatar_url == "https://something/x.jpg"

    @pytest.mark.asyncio
    async def test_avatar_url_built_from_bare_key(self, service, repo):
        repo.carousel_rows = [{
            "author_id": 88, "author_name": "X",
            "author_avatar": "Profile_pics/user-88.jpg",
            "all_viewed": 0, "story_count": 1,
            "latest_at": datetime(2026, 5, 25, 10, 0), "is_friend": 1,
        }]
        result = await service.get_home_carousel(42)
        assert result[0].avatar_url.startswith("https://")
        assert "Profile_pics/user-88.jpg" in result[0].avatar_url


class TestStoriesForClient:
    @pytest.mark.asyncio
    async def test_returns_stories(self, service, repo):
        repo.by_client = {
            "author_id": 88,
            "author_name": "Selena",
            "author_avatar": "https://cdn/selena.jpg",
            "stories": [
                {
                    "story_id": 51, "media_type": "image",
                    "s3_key": "gym_mate/stories/88/a.jpg",
                    "thumbnail_key": None, "caption": "hi",
                    "created_at": datetime(2026, 5, 25, 10, 0),
                    "expires_at": datetime(2026, 5, 26, 10, 0),
                    "is_viewed": False,
                },
                {
                    "story_id": 52, "media_type": "image",
                    "s3_key": "gym_mate/stories/88/b.jpg",
                    "thumbnail_key": None, "caption": None,
                    "created_at": datetime(2026, 5, 25, 11, 0),
                    "expires_at": datetime(2026, 5, 26, 11, 0),
                    "is_viewed": True,
                },
            ],
        }
        result = await service.get_stories_for_client(viewer_id=42, author_id=88)
        assert result.client.name == "Selena"
        assert len(result.stories) == 2
        assert result.stories[0].is_viewed is False
        assert result.stories[1].is_viewed is True
        assert result.stories[0].cdn_url.endswith("/gym_mate/stories/88/a.jpg")

    @pytest.mark.asyncio
    async def test_no_active_stories_404(self, service, repo):
        repo.by_client = None
        with pytest.raises(FittbotHTTPException) as exc:
            await service.get_stories_for_client(viewer_id=42, author_id=88)
        assert exc.value.status_code == 404
        assert exc.value.error_code == "GYMMATE_STORY_NONE_ACTIVE"

    @pytest.mark.asyncio
    async def test_empty_story_list_404(self, service, repo):
        repo.by_client = {
            "author_id": 88, "author_name": "X",
            "author_avatar": None, "stories": [],
        }
        with pytest.raises(FittbotHTTPException):
            await service.get_stories_for_client(viewer_id=42, author_id=88)


class TestRecordView:
    @pytest.mark.asyncio
    async def test_records_when_allowed(self, service, repo):
        repo.record_view_returns = True
        await service.record_view(viewer_id=42, story_id=51)
        assert repo.recorded == [(42, 51)]

    @pytest.mark.asyncio
    async def test_forbidden_when_not_visible(self, service, repo):
        repo.record_view_returns = False
        with pytest.raises(FittbotHTTPException) as exc:
            await service.record_view(viewer_id=42, story_id=51)
        assert exc.value.status_code == 404
        assert exc.value.error_code == "GYMMATE_STORY_VIEW_FORBIDDEN"


class TestOwnerCacheBust:
    @pytest.mark.asyncio
    async def test_create_invokes_owner_change(self):
        calls = []

        async def cb(client_id):
            calls.append(client_id)

        repo = FakeRepo()

        class StoringRepo(FakeRepo):
            async def add(self, story):
                story.id = 1
                return story

        svc = StoryService(
            repository=StoringRepo(),
            event_bus=NoopEventBus(),
            storage=StoryMediaStorage(),
            on_owner_change=cb,
        )
        await svc.create_story(client_id=42, s3_key="gym_mate/stories/42/x.jpg")
        assert calls == [42]
