from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .api import FriendsAPI, build_friends_api
from ._http_schemas import (
    AcceptResponse,
    CancelResponse,
    DiscoverProfilesResponse,
    FriendSuggestionsResponse,
    FriendsListResponse,
    IncomingRequestsResponse,
    OutgoingRequestsResponse,
    RejectResponse,
    SendFriendRequestBody,
    SendFriendRequestResponse,
    UnfriendResponse,
)


router = APIRouter(prefix="/gym_mate/friends", tags=["GymMate Friends V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> FriendsAPI:
    # Wire home-cache invalidation so request lifecycle / friendship
    # changes from any endpoint here bust the affected clients' home
    # cache (both parties for every event). Two homes share these
    # friendship signals: the dedicated GymMate home AND the Fymble home
    # (whose user_state caches the 5 suggested friends), so bust both.
    from app.fittbot_api.v2.Fymble.gym_mate.home._cache import make_home_invalidator
    from app.fittbot_api.v2.Fymble.home.repository import invalidate_user_state_cache

    gymmate_invalidator = make_home_invalidator(redis)

    async def on_change(client_id: int) -> None:
        await gymmate_invalidator(client_id)              # GymMate home cache
        await invalidate_user_state_cache(redis, client_id)  # Fymble home suggestions

    try:
        from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
            gymmate_event_bus,
        )
        return build_friends_api(
            db, redis,
            event_bus=gymmate_event_bus,
            on_change=on_change,
        )
    except ImportError:
        return build_friends_api(db, redis, on_change=on_change)


@router.get("/suggestions", response_model=FriendSuggestionsResponse)
@log_exceptions
async def list_friend_suggestions(
    request: Request,
    limit: int = Query(
        30, ge=1, le=100,
        description="How many suggestions to return. Default 30 for the "
                    "'View all' screen; /home embeds only 5.",
    ),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    """View-all friend suggestions — same 3-tier waterfall as the home
    embed (mutual → match → fallback). Already excludes self, current
    friends, anyone with a pending request either direction, and blocks.

    Slim DTO: name + dp + mutual_count (if mutual) + match_percentage
    (if match). No `details`, no `suggestion_type` — frontend branches
    on which of mutual_count / match_percentage is set.
    """
    from .schemas import FriendSuggestionSlimDTO

    rows = await api.suggest_for_home(client_id=client_id, limit=limit)
    slim = [
        FriendSuggestionSlimDTO(
            sno=r.sno,
            client_id=r.client_id,
            name=r.name,
            avatar_url=r.avatar_url,
            mutual_count=r.mutual_count,
            match_percentage=r.match_percentage,
        )
        for r in rows
    ]
    return FriendSuggestionsResponse(data=slim)


@router.get("/discover", response_model=DiscoverProfilesResponse)
@log_exceptions
async def discover_profiles(
    request: Request,
    limit: int = Query(
        50, ge=1, le=100,
        description="Deck size for the swipe-to-connect screen. Default 50.",
    ),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    """Swipe-to-connect deck shown right after profile creation.

    Best-first waterfall, deduped: highest profile matches first, then
    onboarded users in the same city, then a random sample of the newest
    50 profiles so a brand-new user still lands on a populated deck.

    Excludes self, current friends, anyone with a pending request either
    direction, and blocks. Each card carries `suggestion_type`
    ("match" | "same_city" | "fallback"), `match_percentage` (match tier
    only), and `city`. Returns whatever is available, up to `limit`.
    """
    rows = await api.discover_profiles(client_id=client_id, limit=limit)
    return DiscoverProfilesResponse(data=rows)


@router.post("/requests", response_model=SendFriendRequestResponse)
@log_exceptions
async def send_friend_request(
    req: SendFriendRequestBody,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    data = await api.send_request(
        from_client_id=client_id,
        to_client_id=req.to_client_id,
    )
    await db.commit()
    return SendFriendRequestResponse(data=data)


@router.get("/requests/incoming", response_model=IncomingRequestsResponse)
@log_exceptions
async def list_incoming_requests(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    rows = await api.list_incoming(client_id=client_id)

    try:
        from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
            build_notifications_api,
        )
        notif_api = build_notifications_api(db)
        await notif_api.mark_bucket_read(
            recipient_client_id=client_id, bucket="friend_requests",
        )
        await db.commit()
    except Exception:
        pass
    return IncomingRequestsResponse(data=rows)


@router.get("/requests/outgoing", response_model=OutgoingRequestsResponse)
@log_exceptions
async def list_outgoing_requests(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    rows = await api.list_outgoing(client_id=client_id)
    return OutgoingRequestsResponse(data=rows)


@router.post("/requests/{request_id}/accept", response_model=AcceptResponse)
@log_exceptions
async def accept_friend_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    await api.accept_request(recipient_id=client_id, request_id=request_id)
    await db.commit()
    return AcceptResponse()


@router.post("/requests/{request_id}/reject", response_model=RejectResponse)
@log_exceptions
async def reject_friend_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    await api.reject_request(recipient_id=client_id, request_id=request_id)
    await db.commit()
    return RejectResponse()


@router.delete("/requests/{request_id}", response_model=CancelResponse)
@log_exceptions
async def cancel_friend_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    await api.cancel_request(sender_id=client_id, request_id=request_id)
    await db.commit()
    return CancelResponse()


@router.get("", response_model=FriendsListResponse)
@log_exceptions
async def list_my_friends(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    rows = await api.list_friends(client_id=client_id)
    return FriendsListResponse(data=rows)


@router.delete("/{other_client_id}", response_model=UnfriendResponse)
@log_exceptions
async def unfriend(
    other_client_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: FriendsAPI = Depends(_api),
):
    await api.unfriend(client_id=client_id, other_id=other_client_id)
    await db.commit()
    return UnfriendResponse()
