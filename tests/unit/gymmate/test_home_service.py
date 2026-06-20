from datetime import datetime
from typing import List

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.home._cache import HomeCache, _key
from app.fittbot_api.v2.Fymble.gym_mate.home._service import HomeService
from app.fittbot_api.v2.Fymble.gym_mate.home.schemas import HomeDTO, HomeStoriesDTO
from app.fittbot_api.v2.Fymble.gym_mate.sessions.schemas import (
    HostIdentityDTO,
    HostSessionsSummaryDTO,
    ReceivedRequestsSummaryDTO,
)
from app.fittbot_api.v2.Fymble.gym_mate.stories.schemas import (
    CarouselAuthorDTO,
    MyStorySummaryDTO,
)


EMPTY_SESSIONS = HostSessionsSummaryDTO(
    host=HostIdentityDTO(client_id=42),
    future_count=0,
    received_requests=ReceivedRequestsSummaryDTO(pending_count=0, recent_avatars=[]),
    matches=[],
)


class StubStoriesAPI:
    def __init__(self):
        self.my_story = MyStorySummaryDTO(client_id=42, name=None, has_active=False, all_viewed=True)
        self.carousel: List[CarouselAuthorDTO] = []
        self.calls_my_story = 0
        self.calls_carousel = 0

    async def get_my_story_summary(self, client_id):
        self.calls_my_story += 1
        return self.my_story

    async def get_home_carousel(self, viewer_id, limit=20):
        self.calls_carousel += 1
        return self.carousel


class StubSessionsAPI:
    def __init__(self, summary: HostSessionsSummaryDTO = EMPTY_SESSIONS):
        self.summary = summary
        self.calls = 0
        self.last_host_identity = None
        self.nearby_calls = 0
        self.last_distance_map = None
        self.nearby_return = []

    async def get_host_summary(self, host_client_id, host_identity=None):
        self.calls += 1
        self.last_host_identity = host_identity
        if host_identity is not None:
            return self.summary.model_copy(update={"host": host_identity})
        return self.summary

    async def list_nearby_gym_mates(self, viewer_client_id, distance_map, limit=20):
        self.nearby_calls += 1
        self.last_distance_map = distance_map
        return self.nearby_return


class StubGeoService:
    def __init__(self, distance_map=None):
        self.distance_map = distance_map or {}
        self.hydrate_calls = 0
        self.search_calls = 0

    async def hydrate(self, db):
        self.hydrate_calls += 1
        return True

    async def get_nearby_distances(self, lat, lng, radius_km, count=1000):
        self.search_calls += 1
        return self.distance_map


class StubFriendsAPI:
    def __init__(self):
        self.calls = 0
        self.returns = []

    async def suggest_for_home(self, client_id, limit=5):
        self.calls += 1
        return self.returns


class TestHomeService:
    @pytest.mark.asyncio
    async def test_cold_load_calls_stories(self, fake_redis):
        stories = StubStoriesAPI()
        sessions = StubSessionsAPI()
        geo = StubGeoService()
        svc = HomeService(
            db=None, redis=None, stories_api=stories, sessions_api=sessions, friends_api=StubFriendsAPI(),
            geo_service=geo, cache=HomeCache(fake_redis),
        )

        result = await svc.get_home(client_id=42, lat=12.97, lng=77.59)
        assert isinstance(result, HomeDTO)
        assert stories.calls_my_story == 1
        assert stories.calls_carousel == 1
        assert sessions.calls == 1
        assert geo.search_calls == 1
        assert await fake_redis.exists(_key(42, 12.97, 77.59)) == 1

    @pytest.mark.asyncio
    async def test_warm_load_uses_cache(self, fake_redis):
        stories = StubStoriesAPI()
        sessions = StubSessionsAPI()
        geo = StubGeoService()
        cache = HomeCache(fake_redis)
        pre = HomeDTO(
            stories=HomeStoriesDTO(
                my_story=MyStorySummaryDTO(
                    client_id=42, name="Raj", has_active=True, all_viewed=False,
                    story_id=99, story_count=1, view_count=3,
                ),
                carousel=[],
            ),
            sessions=EMPTY_SESSIONS,
            nearby_gym_mates=[],
        )
        await cache.set(42, pre, 12.97, 77.59)

        svc = HomeService(
            db=None, redis=None, stories_api=stories, sessions_api=sessions, friends_api=StubFriendsAPI(),
            geo_service=geo, cache=cache,
        )
        result = await svc.get_home(client_id=42, lat=12.97, lng=77.59)
        assert result.stories.my_story.story_id == 99
        assert stories.calls_my_story == 0
        assert stories.calls_carousel == 0
        assert sessions.calls == 0
        assert geo.search_calls == 0

    @pytest.mark.asyncio
    async def test_payload_shape_has_my_story_and_carousel(self, fake_redis):
        stories = StubStoriesAPI()
        sessions = StubSessionsAPI()
        geo = StubGeoService()
        stories.my_story = MyStorySummaryDTO(
            client_id=42, name="Raj", avatar_url="https://x/r.jpg",
            has_active=True, all_viewed=False,
            story_id=12, story_count=2, view_count=5,
        )
        stories.carousel = [
            CarouselAuthorDTO(
                client_id=88, name="Selena", avatar_url=None,
                all_viewed=False, story_count=1,
                latest_at=datetime(2026, 5, 25, 18, 0),
                is_friend=True,
            )
        ]
        svc = HomeService(
            db=None, redis=None, stories_api=stories, sessions_api=sessions, friends_api=StubFriendsAPI(),
            geo_service=geo, cache=HomeCache(fake_redis),
        )
        result = await svc.get_home(client_id=42, lat=12.97, lng=77.59)
        assert result.stories.my_story.story_id == 12
        assert len(result.stories.carousel) == 1
        assert result.stories.carousel[0].client_id == 88
        # Host identity is propagated from my_story so sessions doesn't re-query.
        assert sessions.last_host_identity is not None
        assert sessions.last_host_identity.client_id == 42
        assert sessions.last_host_identity.name == "Raj"
        assert sessions.last_host_identity.avatar_url == "https://x/r.jpg"
        assert result.sessions.host.name == "Raj"
        assert result.sessions.host.avatar_url == "https://x/r.jpg"

    @pytest.mark.asyncio
    async def test_nearby_skipped_when_no_gyms_in_range(self, fake_redis):
        stories = StubStoriesAPI()
        sessions = StubSessionsAPI()
        geo = StubGeoService(distance_map={})
        svc = HomeService(
            db=None, redis=None, stories_api=stories, sessions_api=sessions, friends_api=StubFriendsAPI(),
            geo_service=geo, cache=HomeCache(fake_redis),
        )
        result = await svc.get_home(client_id=42, lat=0.0, lng=0.0)
        assert result.nearby_gym_mates == []
        # GEO was queried, but sessions API was NOT (no gyms in range)
        assert geo.search_calls == 1
        assert sessions.nearby_calls == 0

    @pytest.mark.asyncio
    async def test_nearby_passes_distance_map_to_sessions(self, fake_redis):
        from app.fittbot_api.v2.Fymble.gym_mate.sessions.schemas import NearbyGymMateDTO
        from datetime import date as _date, time as _time

        stories = StubStoriesAPI()
        sessions = StubSessionsAPI()
        sessions.nearby_return = [
            NearbyGymMateDTO(
                sno=1,
                session_id=7, host_client_id=37,
                host_name="Martin", host_avatar_url="https://cdn/m.png",
                gym_id=11, gym_name="MuscleMax",
                distance_km=2.4,
                session_date=_date(2026, 5, 27), session_time=_time(10, 30),
            )
        ]
        geo = StubGeoService(distance_map={11: 2.4, 22: 5.0})
        svc = HomeService(
            db=None, redis=None, stories_api=stories, sessions_api=sessions, friends_api=StubFriendsAPI(),
            geo_service=geo, cache=HomeCache(fake_redis),
        )
        result = await svc.get_home(client_id=42, lat=12.97, lng=77.59)
        assert sessions.nearby_calls == 1
        assert sessions.last_distance_map == {11: 2.4, 22: 5.0}
        assert len(result.nearby_gym_mates) == 1
        assert result.nearby_gym_mates[0].gym_id == 11
        assert result.nearby_gym_mates[0].distance_km == 2.4


class TestHomeCache:
    @pytest.mark.asyncio
    async def test_set_and_get_round_trip(self, fake_redis):
        cache = HomeCache(fake_redis)
        payload = HomeDTO(
            stories=HomeStoriesDTO(
                my_story=MyStorySummaryDTO(client_id=42, has_active=False, all_viewed=True),
                carousel=[],
            ),
            sessions=EMPTY_SESSIONS,
            nearby_gym_mates=[],
        )
        await cache.set(42, payload, 12.97, 77.59)
        got = await cache.get(42, 12.97, 77.59)
        assert got is not None
        assert got.stories.my_story.has_active is False

    @pytest.mark.asyncio
    async def test_cache_key_segregates_by_geo(self, fake_redis):
        cache = HomeCache(fake_redis)
        payload = HomeDTO(
            stories=HomeStoriesDTO(
                my_story=MyStorySummaryDTO(client_id=42, has_active=False, all_viewed=True),
                carousel=[],
            ),
            sessions=EMPTY_SESSIONS,
            nearby_gym_mates=[],
        )
        await cache.set(42, payload, 12.97, 77.59)
        # Different city: same client, different geocell → cache MISS
        assert await cache.get(42, 28.61, 77.20) is None
        # Same cell at ~1km precision → HIT (12.971 rounds to 12.97)
        assert await cache.get(42, 12.971, 77.594) is not None

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, fake_redis):
        cache = HomeCache(fake_redis)
        assert await cache.get(999, 12.97, 77.59) is None

    @pytest.mark.asyncio
    async def test_invalidate_drops_all_geo_cells_for_client(self, fake_redis):
        cache = HomeCache(fake_redis)
        payload = HomeDTO(
            stories=HomeStoriesDTO(
                my_story=MyStorySummaryDTO(client_id=42, has_active=False, all_viewed=True),
                carousel=[],
            ),
            sessions=EMPTY_SESSIONS,
            nearby_gym_mates=[],
        )
        # Same client cached in two different geocells
        await cache.set(42, payload, 12.97, 77.59)
        await cache.set(42, payload, 28.61, 77.20)
        assert await fake_redis.exists(_key(42, 12.97, 77.59)) == 1
        assert await fake_redis.exists(_key(42, 28.61, 77.20)) == 1

        await cache.invalidate(42)
        assert await fake_redis.exists(_key(42, 12.97, 77.59)) == 0
        assert await fake_redis.exists(_key(42, 28.61, 77.20)) == 0

    @pytest.mark.asyncio
    async def test_corrupt_entry_is_purged(self, fake_redis):
        cache = HomeCache(fake_redis)
        await fake_redis.set(_key(42, 12.97, 77.59), "{not json")
        got = await cache.get(42, 12.97, 77.59)
        assert got is None
        assert await fake_redis.exists(_key(42, 12.97, 77.59)) == 0
