from datetime import date, time
from typing import Awaitable, Callable, Dict, List, Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import (
    HostedSessionDTO,
    HostIdentityDTO,
    HostSessionsSummaryDTO,
    MatchedSessionDTO,
    MatchListItemDTO,
    NearbyGymMateAllDTO,
    NearbyGymMateDTO,
    PendingRequestDTO,
    SentRequestDTO,
    SessionDTO,
    SessionParticipantDTO,
    SessionRequestDTO,
)
from ._events import EventBus, NoopEventBus


OnChange = Optional[Callable[[int], Awaitable[None]]]


class SessionsAPI(Protocol):

    async def create_session(
        self,
        host_client_id: int,
        gym_id: int,
        session_date: date,
        session_time: time,
        mate_preference: str,
        fitness_level: str,
        workout_vibes: List[str],
        payment_mode: str = "pay_later",
    ) -> SessionDTO: ...

    async def get_session(
        self,
        requester_client_id: int,
        session_id: int,
    ) -> SessionDTO: ...

    async def cancel_session(
        self,
        requester_client_id: int,
        session_id: int,
    ) -> None: ...

    async def mark_paid_via_webhook(
        self,
        session_id: int,
        daily_pass_id: str,
    ) -> None: ...

    async def send_request(
        self,
        requester_client_id: int,
        session_id: int,
        message: Optional[str] = None,
    ) -> SessionRequestDTO: ...

    async def accept_request(
        self,
        host_client_id: int,
        request_id: int,
    ) -> None: ...

    async def reject_request(
        self,
        host_client_id: int,
        request_id: int,
    ) -> None: ...

    async def withdraw_request(
        self,
        requester_client_id: int,
        request_id: int,
    ) -> None: ...

    async def list_pending_for_session(
        self,
        requester_client_id: int,
        session_id: int,
    ) -> List[PendingRequestDTO]: ...

    async def list_session_participants(
        self,
        viewer_client_id: int,
        session_id: int,
    ) -> List[SessionParticipantDTO]: ...

    async def list_inbox(
        self,
        host_client_id: int,
    ) -> List[PendingRequestDTO]: ...

    async def list_sent(
        self,
        client_id: int,
    ) -> List[SentRequestDTO]: ...

    async def list_my_matches(
        self,
        client_id: int,
    ) -> List[MatchedSessionDTO]: ...

    async def list_hosted_sessions(
        self,
        host_client_id: int,
    ) -> List[HostedSessionDTO]: ...

    async def list_all_nearby_gym_mates(
        self,
        viewer_client_id: int,
        distance_map: Dict[int, float],
        limit: int = 100,
        viewer_gender: Optional[str] = None,
    ) -> List[NearbyGymMateAllDTO]: ...

    async def get_host_summary(
        self,
        host_client_id: int,
        host_identity: Optional[HostIdentityDTO] = None,
    ) -> HostSessionsSummaryDTO: ...

    async def list_nearby_gym_mates(
        self,
        viewer_client_id: int,
        distance_map: Dict[int, float],
        limit: int = 20,
        viewer_gender: Optional[str] = None,
    ) -> List[NearbyGymMateDTO]: ...


def build_sessions_api(
    db: AsyncSession,
    redis: Optional[Redis] = None,
    *,
    event_bus: Optional[EventBus] = None,
    on_change: OnChange = None,
) -> SessionsAPI:
    from ._repository import SessionRepository, SessionRequestRepository
    from ._service import SessionService

    return SessionService(
        repository=SessionRepository(db),
        request_repository=SessionRequestRepository(db),
        event_bus=event_bus or NoopEventBus(),
        on_change=on_change,
        db=db,
        redis=redis,
    )
