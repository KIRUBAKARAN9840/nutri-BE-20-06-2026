from typing import Awaitable, Callable, List, Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ._events import EventBus, NoopEventBus
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


OnChange = Optional[Callable[[int], Awaitable[None]]]


class FriendsAPI(Protocol):
    # Suggestions
    async def suggest_for_home(
        self,
        client_id: int,
        limit: int = 5,
        extra_exclude: Optional[set] = None,
    ) -> List[FriendSuggestionDTO]: ...

    async def discover_profiles(
        self, client_id: int, limit: int = 30,
    ) -> List[DiscoverProfileDTO]: ...

    # Request lifecycle
    async def send_request(
        self, from_client_id: int, to_client_id: int,
    ) -> FriendRequestDTO: ...

    async def accept_request(self, recipient_id: int, request_id: int) -> None: ...
    async def reject_request(self, recipient_id: int, request_id: int) -> None: ...
    async def cancel_request(self, sender_id: int, request_id: int) -> None: ...

    # Listings
    async def list_incoming(self, client_id: int) -> List[IncomingRequestDTO]: ...
    async def list_outgoing(self, client_id: int) -> List[OutgoingRequestDTO]: ...
    async def list_friends(self, client_id: int) -> List[FriendDTO]: ...

    async def recent_request_sender_avatars(
        self, client_id: int, limit: int = 3,
    ) -> List[str]: ...

    async def list_mutual_friends(
        self, viewer_id: int, target_id: int, limit: int = 3,
    ) -> List[MutualFriendDTO]: ...

    async def get_relationship(
        self, viewer_id: int, target_id: int,
    ) -> RelationshipDTO: ...

    async def get_onboarding_step2_suggestions(
        self, client_id: int,
    ) -> List[OnboardingSuggestionDTO]: ...

    async def get_match_info(
        self, viewer_id: int, target_id: int,
    ) -> Optional[MatchInfoDTO]: ...

    # Friendship
    async def unfriend(self, client_id: int, other_id: int) -> None: ...


def build_friends_api(
    db: AsyncSession,
    redis: Optional[Redis] = None,
    *,
    event_bus: Optional[EventBus] = None,
    on_change: OnChange = None,
) -> FriendsAPI:
    from ._repository import FriendRepository
    from ._rotation import RotationCache
    from ._service import FriendsService

    return FriendsService(
        repository=FriendRepository(db),
        event_bus=event_bus or NoopEventBus(),
        on_change=on_change,
        rotation=RotationCache(redis) if redis is not None else None,
    )
