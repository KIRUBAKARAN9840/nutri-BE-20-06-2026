from typing import Dict, List, Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v2.Fymble.fitness_studios.shared.geo_service import GeoService
from app.fittbot_api.v2.Fymble.fitness_studios.shared.gym_price_enricher import (
    fetch_gym_info,
)
from app.fittbot_api.v2.Fymble.gym_mate.friends import FriendsAPI
from app.fittbot_api.v2.Fymble.gym_mate.notifications import NotificationsAPI
from app.fittbot_api.v2.Fymble.gym_mate.sessions import (
    HostIdentityDTO,
    SessionsAPI,
)
from app.fittbot_api.v2.Fymble.gym_mate.stories import StoriesAPI

from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
    HomeNotificationsSummaryDTO,
)

from ._cache import HomeCache
from .schemas import HomeDTO, HomeStoriesDTO, NearbyGymDTO


CAROUSEL_LIMIT = 20
NEARBY_RADIUS_KM = 30.0
NEARBY_GYM_MATES_LIMIT = 10
NEARBY_GYMS_LIMIT = 5
FRIEND_SUGGESTIONS_LIMIT = 5



class HomeService:
    def __init__(
        self,
        db: AsyncSession,
        redis: Optional[Redis],
        stories_api: StoriesAPI,
        sessions_api: SessionsAPI,
        friends_api: FriendsAPI,
        geo_service: Optional[GeoService],
        cache: HomeCache,
        notifications_api: Optional[NotificationsAPI] = None,
    ):
        self.db = db
        self.redis = redis
        self.stories = stories_api
        self.sessions = sessions_api
        self.friends = friends_api
        self.notifications = notifications_api
        self.geo = geo_service
        self.cache = cache

    async def get_home(
        self,
        client_id: int,
        lat: float,
        lng: float,
    ) -> HomeDTO:
        cached = await self.cache.get(client_id, lat, lng)
        if cached is not None:
            return cached

        my_story = await self.stories.get_my_story_summary(client_id)
        host_identity = HostIdentityDTO(
            client_id=my_story.client_id,
            name=my_story.name,
            avatar_url=my_story.avatar_url,
        )

        carousel = await self.stories.get_home_carousel(client_id, limit=CAROUSEL_LIMIT)
        sessions_summary = await self.sessions.get_host_summary(
            host_client_id=client_id,
            host_identity=host_identity,
        )

        distance_map = await self._get_distance_map(lat, lng)
        # nearby_gym_mates and nearby_gyms share the same GEOSEARCH result.
        nearby_gym_mates = (
            await self.sessions.list_nearby_gym_mates(
                viewer_client_id=client_id,
                distance_map=distance_map,
                limit=NEARBY_GYM_MATES_LIMIT,
            )
            if distance_map else []
        )
        nearby_gyms = await self._get_nearby_gyms(distance_map)

        friend_suggestions = await self.friends.suggest_for_home(
            client_id=client_id, limit=FRIEND_SUGGESTIONS_LIMIT,
        )

        # Unread tallies for the three home dots — single SQL via the
        # notifications module. Safe-default (all zero) when the API
        # isn't wired (older callers / tests).
        notifications = HomeNotificationsSummaryDTO()
        if self.notifications is not None:
            notifications = await self.notifications.home_summary(
                recipient_client_id=client_id,
            )
            # Enrich the friend_requests bucket with the top-3 sender
            # DPs. The notifications module only knows counts; the
            # avatars come from friends/repo. Only query when there's at
            # least one — saves a wasted round trip.
            if notifications.friend_requests.count > 0 and self.friends is not None:
                avatars = await self.friends.recent_request_sender_avatars(
                    client_id=client_id, limit=3,
                )
                notifications = notifications.model_copy(update={
                    "friend_requests": notifications.friend_requests.model_copy(
                        update={"recent_avatars": avatars},
                    ),
                })

        payload = HomeDTO(
            stories=HomeStoriesDTO(my_story=my_story, carousel=carousel),
            sessions=sessions_summary,
            nearby_gym_mates=nearby_gym_mates,
            nearby_gyms=nearby_gyms,
            friend_suggestions=friend_suggestions,
            notifications=notifications,
        )
        await self.cache.set(client_id, payload, lat, lng)
        return payload

    async def _get_distance_map(self, lat: float, lng: float) -> Dict[int, float]:
        if self.geo is None:
            return {}
        await self.geo.hydrate(self.db)
        return await self.geo.get_nearby_distances(
            lat=lat, lng=lng, radius_km=NEARBY_RADIUS_KM,
        )

    async def _get_nearby_gyms(
        self, distance_map: Dict[int, float]
    ) -> List[NearbyGymDTO]:
        """Top-N nearest gyms with photo, area, distance, dailypass price.

        Uses the same Redis GEOSEARCH result that nearby_gym_mates uses
        (no extra geo query) and the shared `fetch_gym_info` helper for
        gym info + dailypass pricing (no duplicate logic).
        """
        if not distance_map or self.redis is None:
            return []

        top_ids = sorted(distance_map.keys(), key=distance_map.get)[
            :NEARBY_GYMS_LIMIT
        ]
        info_map = await fetch_gym_info(self.db, self.redis, top_ids)

        out: List[NearbyGymDTO] = []
        for i, gid in enumerate(top_ids, start=1):
            gi = info_map.get(gid)
            if gi is None:
                continue
            out.append(NearbyGymDTO(
                sno=i,
                gym_id=gid,
                gym_name=gi.name,
                gym_area=gi.area,
                cover_pic=gi.cover_pic,
                distance_km=round(distance_map[gid], 2),
                dailypass_price=gi.dailypass_price,
            ))
        return out
