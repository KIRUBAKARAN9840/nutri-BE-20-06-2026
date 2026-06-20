from typing import List, Optional

from pydantic import BaseModel

from app.fittbot_api.v2.Fymble.gym_mate.friends import FriendSuggestionDTO
from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
    HomeNotificationsSummaryDTO,
)
from app.fittbot_api.v2.Fymble.gym_mate.sessions import (
    HostSessionsSummaryDTO,
    NearbyGymMateDTO,
)
from app.fittbot_api.v2.Fymble.gym_mate.stories import (
    CarouselAuthorDTO,
    MyStorySummaryDTO,
)


class HomeStoriesDTO(BaseModel):
    my_story: MyStorySummaryDTO
    carousel: List[CarouselAuthorDTO]


class NearbyGymDTO(BaseModel):
    sno: int
    gym_id: int
    gym_name: Optional[str] = None
    gym_area: Optional[str] = None
    cover_pic: Optional[str] = None
    distance_km: float
    dailypass_price: Optional[int] = None


class HomeDTO(BaseModel):
    stories: HomeStoriesDTO
    sessions: HostSessionsSummaryDTO
    nearby_gym_mates: List[NearbyGymMateDTO] = []
    nearby_gyms: List[NearbyGymDTO] = []
    friend_suggestions: List[FriendSuggestionDTO] = []
    # Per-bucket unread tally for the home-page dots (gym_mate
    # connections, friend requests, chat). See HomeNotificationsSummaryDTO
    # for the shape. Default = all-zero so frontends that don't read
    # this field don't have to handle a missing key.
    notifications: HomeNotificationsSummaryDTO = HomeNotificationsSummaryDTO()
