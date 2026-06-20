from typing import Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import HomeDTO


class HomeAPI(Protocol):
    async def get_home(
        self, client_id: int, lat: float, lng: float,
    ) -> HomeDTO: ...


def build_home_api(
    db: AsyncSession,
    redis: Optional[Redis] = None,
) -> HomeAPI:
    from app.fittbot_api.v2.Fymble.fitness_studios.shared.geo_service import GeoService
    from app.fittbot_api.v2.Fymble.gym_mate.friends import build_friends_api
    from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
        build_notifications_api,
    )
    from app.fittbot_api.v2.Fymble.gym_mate.sessions import build_sessions_api
    from app.fittbot_api.v2.Fymble.gym_mate.stories import build_stories_api

    from ._cache import HomeCache, make_home_invalidator
    from ._service import HomeService

    invalidator = make_home_invalidator(redis)
    stories = build_stories_api(db, redis, on_owner_change=invalidator)
    sessions = build_sessions_api(db, redis, on_change=invalidator)
    friends = build_friends_api(db, redis, on_change=invalidator)
    notifications = build_notifications_api(db)
    geo = GeoService(redis) if redis is not None else None
    return HomeService(
        db=db,
        redis=redis,
        stories_api=stories,
        sessions_api=sessions,
        friends_api=friends,
        notifications_api=notifications,
        geo_service=geo,
        cache=HomeCache(redis),
    )
