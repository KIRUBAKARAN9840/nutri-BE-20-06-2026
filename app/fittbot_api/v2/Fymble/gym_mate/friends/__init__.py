from .api import FriendsAPI, build_friends_api
from .routes import router
from .schemas import (
    DiscoverProfileDTO,
    FriendDTO,
    FriendRequestDTO,
    FriendSuggestionDTO,
    IncomingRequestDTO,
    MatchInfoDTO,
    MutualFriendDTO,
    OnboardingSuggestionDTO,
    OutgoingRequestDTO,
    RelationshipDTO,
)

__all__ = [
    "FriendsAPI",
    "build_friends_api",
    "DiscoverProfileDTO",
    "FriendSuggestionDTO",
    "FriendRequestDTO",
    "IncomingRequestDTO",
    "OutgoingRequestDTO",
    "FriendDTO",
    "MutualFriendDTO",
    "RelationshipDTO",
    "MatchInfoDTO",
    "OnboardingSuggestionDTO",
    "router",
]
