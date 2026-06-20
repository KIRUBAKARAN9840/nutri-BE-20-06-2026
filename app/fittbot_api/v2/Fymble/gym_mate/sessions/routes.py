from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .api import SessionsAPI, build_sessions_api
from ._http_schemas import (
    AcceptRequestResponse,
    CancelSessionResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    GetSessionResponse,
    HostedSessionsResponse,
    HostSummaryResponse,
    InboxResponse,
    ListSessionRequestsResponse,
    MyMatchesResponse,
    NearbyGymMatesAllResponse,
    RejectRequestResponse,
    SendRequestBody,
    SendRequestResponse,
    SentRequestsResponse,
    SessionParticipantsResponse,
    WithdrawRequestResponse,
)


router = APIRouter(prefix="/gym_mate/sessions", tags=["GymMate Sessions V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> SessionsAPI:

    from app.fittbot_api.v2.Fymble.gym_mate.home._cache import make_home_invalidator
    try:
        from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
            gymmate_event_bus,
        )
        return build_sessions_api(
            db, redis,
            event_bus=gymmate_event_bus,
            on_change=make_home_invalidator(redis),
        )
    except ImportError:
        return build_sessions_api(
            db, redis, on_change=make_home_invalidator(redis),
        )


@router.post("", response_model=CreateSessionResponse)
@log_exceptions
async def create_session(
    req: CreateSessionRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    session = await api.create_session(
        host_client_id=client_id,
        gym_id=req.gym_id,
        session_date=req.session_date,
        session_time=req.session_time,
        mate_preference=req.mate_preference,
        fitness_level=req.fitness_level,
        workout_vibes=req.workout_vibes,
        payment_mode=req.payment_mode,
    )
    await db.commit()
    return CreateSessionResponse(data=session)


@router.get("/me/summary", response_model=HostSummaryResponse)
@log_exceptions
async def get_my_session_summary(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    summary = await api.get_host_summary(host_client_id=client_id)
    return HostSummaryResponse(data=summary)


@router.get("/me/inbox", response_model=InboxResponse)
@log_exceptions
async def get_my_inbox(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    rows = await api.list_inbox(host_client_id=client_id)
    return InboxResponse(data=rows)


@router.get("/me/received", response_model=InboxResponse)
@log_exceptions
async def get_my_received_requests(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):

    rows = await api.list_inbox(host_client_id=client_id)
    return InboxResponse(data=rows)


@router.get("/me/sent", response_model=SentRequestsResponse)
@log_exceptions
async def get_my_sent_requests(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):

    rows = await api.list_sent(client_id=client_id)
    return SentRequestsResponse(data=rows)


@router.get("/me/matches", response_model=MyMatchesResponse)
@log_exceptions
async def get_my_matches(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):

    rows = await api.list_my_matches(client_id=client_id)
    return MyMatchesResponse(data=rows)


@router.get("/me/hosted", response_model=HostedSessionsResponse)
@log_exceptions
async def get_my_hosted_sessions(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    """The viewer's OWN open future sessions with all create-time
    details + joiner count. Sorted by nearest first.

    Cancel a session via DELETE /api/v2/gym_mate/sessions/{session_id}
    (existing endpoint, works regardless of joiner_count)."""
    rows = await api.list_hosted_sessions(host_client_id=client_id)
    return HostedSessionsResponse(data=rows)


@router.get("/nearby", response_model=NearbyGymMatesAllResponse)
@log_exceptions
async def get_nearby_gym_mates_view_all(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    limit: int = Query(
        100, ge=1, le=200,
        description="How many sessions to return. The home embed is "
                    "capped at 10; this view-all defaults to 100.",
    ),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    """View-all nearby gym_mate sessions within 30km. Same waterfall
    as the home embed but unlimited (cap 200) AND with each row's
    `request_status` so the frontend knows whether to show 'Connect'
    or 'Request Sent'.

    Sorted by distance (nearest gym first) then by soonest session.
    """
    from app.fittbot_api.v2.Fymble.fitness_studios.shared.geo_service import (
        GeoService,
    )

    geo = GeoService(redis)
    await geo.hydrate(db)
    distance_map = await geo.get_nearby_distances(
        lat=lat, lng=lng, radius_km=30.0,
    )
    rows = await api.list_all_nearby_gym_mates(
        viewer_client_id=client_id,
        distance_map=distance_map,
        limit=limit,
    )
    return NearbyGymMatesAllResponse(data=rows)


@router.get("/{session_id}", response_model=GetSessionResponse)
@log_exceptions
async def get_session(
    session_id: int,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    session = await api.get_session(requester_client_id=client_id, session_id=session_id)
    return GetSessionResponse(data=session)


@router.delete("/{session_id}", response_model=CancelSessionResponse)
@log_exceptions
async def cancel_session(
    session_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    await api.cancel_session(requester_client_id=client_id, session_id=session_id)
    await db.commit()
    return CancelSessionResponse()


@router.get("/{session_id}/participants", response_model=SessionParticipantsResponse)
@log_exceptions
async def get_session_participants(
    session_id: int,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    """All accepted members of a session (host + joiners) with name + DP.
    Shown on the session-detail modal when a card is tapped."""
    rows = await api.list_session_participants(
        viewer_client_id=client_id, session_id=session_id,
    )
    return SessionParticipantsResponse(data=rows)


@router.post("/{session_id}/requests", response_model=SendRequestResponse)
@log_exceptions
async def send_join_request(
    session_id: int,
    request: Request,
    req: Optional[SendRequestBody] = Body(default=None),
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    data = await api.send_request(
        requester_client_id=client_id,
        session_id=session_id,
        message=req.message if req else None,
    )
    await db.commit()
    return SendRequestResponse(data=data)


@router.get("/{session_id}/requests", response_model=ListSessionRequestsResponse)
@log_exceptions
async def list_session_pending_requests(
    session_id: int,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    rows = await api.list_pending_for_session(
        requester_client_id=client_id, session_id=session_id
    )
    return ListSessionRequestsResponse(data=rows)


@router.post("/requests/{request_id}/accept", response_model=AcceptRequestResponse)
@log_exceptions
async def accept_join_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    await api.accept_request(host_client_id=client_id, request_id=request_id)
    await db.commit()
    return AcceptRequestResponse()


@router.post("/requests/{request_id}/reject", response_model=RejectRequestResponse)
@log_exceptions
async def reject_join_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    await api.reject_request(host_client_id=client_id, request_id=request_id)
    await db.commit()
    return RejectRequestResponse()


@router.delete("/requests/{request_id}", response_model=WithdrawRequestResponse)
@log_exceptions
async def withdraw_join_request(
    request_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: SessionsAPI = Depends(_api),
):
    await api.withdraw_request(requester_client_id=client_id, request_id=request_id)
    await db.commit()
    return WithdrawRequestResponse()
