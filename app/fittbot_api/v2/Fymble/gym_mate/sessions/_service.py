from datetime import date, time
from typing import Awaitable, Callable, Dict, List, Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._events import (
    EventBus,
    SessionCancelled,
    SessionCreated,
    SessionPaid,
    SessionRequestAccepted,
    SessionRequestCreated,
    SessionRequestRejected,
    SessionRequestWithdrawn,
)
from ._repository import SessionRepository, SessionRequestRepository


OnChange = Optional[Callable[[int], Awaitable[None]]]


def _avatar_url_or_none(value: Optional[str]) -> Optional[str]:
    """Pipe stored s3_path / clients.profile through the CDN URL builder.
    Pre-existing http(s) URLs (dummy DPs from seed data) pass through."""
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    from app.fittbot_api.v2.Fymble.gym_mate.profile._storage import build_cdn_url
    return build_cdn_url(value)


class SessionService:
    def __init__(
        self,
        repository: SessionRepository,
        request_repository: SessionRequestRepository,
        event_bus: EventBus,
        on_change: OnChange = None,
        db=None,
        redis=None,
    ):
        self.repo = repository
        self.req_repo = request_repository
        self.bus = event_bus
        self._on_change = on_change
        # db + redis are needed by list_my_matches to enrich gyms with
        # cover_pic + dailypass_price via the shared helper. Both
        # optional so unit tests that pass only the repos still work.
        self.db = db
        self.redis = redis

    async def _fire(self, *client_ids: int) -> None:
        if self._on_change is None:
            return
        for cid in {c for c in client_ids if c is not None}:
            await self._on_change(cid)

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
    ) -> dto.SessionDTO:
        try:
            mp = d.MatePreference(mate_preference)
            fl = d.FitnessLevel(fitness_level)
            vibes = d.WorkoutVibes(tuple(workout_vibes))
            mode = d.PaymentMode(payment_mode)
        except (ValueError, d.SessionDomainError) as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_SESSION_INVALID",
                log_data={"client_id": host_client_id, "exc": repr(exc)},
            )

        if mode == d.PaymentMode.PAY_LATER:
            session = d.Session.create_pay_later(
                host_client_id=host_client_id,
                gym_id=gym_id,
                session_date=session_date,
                session_time=session_time,
                mate_preference=mp,
                fitness_level=fl,
                workout_vibes=vibes,
            )
        else:
            session = d.Session.create_pay_now(
                host_client_id=host_client_id,
                gym_id=gym_id,
                session_date=session_date,
                session_time=session_time,
                mate_preference=mp,
                fitness_level=fl,
                workout_vibes=vibes,
            )

        await self.repo.add(session)
        # Host is implicitly a member of their own session.
        await self.req_repo.add_member(session.id, host_client_id, role="host")

        await self.bus.publish(SessionCreated(
            session_id=session.id,
            host_client_id=host_client_id,
            gym_id=gym_id,
            session_date=session_date,
            session_time=session_time,
            payment_mode=session.payment_mode.value,
            payment_status=session.payment_status.value,
        ))
        await self._fire(host_client_id)
        return self._to_dto(session)

    async def get_session(self, requester_client_id: int, session_id: int) -> dto.SessionDTO:
        session = await self.repo.get_by_id(session_id)
        if session is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Session not found",
                error_code="GYMMATE_SESSION_NOT_FOUND",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )
        return self._to_dto(session)

    async def cancel_session(self, requester_client_id: int, session_id: int) -> None:
        session = await self.repo.get_by_id(session_id)
        if session is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Session not found",
                error_code="GYMMATE_SESSION_NOT_FOUND",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )
        try:
            session.cancel(requester_client_id)
        except d.SessionNotOwned as exc:
            raise FittbotHTTPException(
                status_code=403,
                detail=str(exc),
                error_code="GYMMATE_SESSION_FORBIDDEN",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )
        except (d.SessionAlreadyCancelled, d.InvalidStateTransition) as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_SESSION_BAD_STATE",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )

        await self.repo.update_status(session_id, session.status.value)
        await self.bus.publish(SessionCancelled(
            session_id=session_id,
            host_client_id=requester_client_id,
        ))
        await self._fire(requester_client_id)

    async def mark_paid_via_webhook(self, session_id: int, daily_pass_id: str) -> None:
        session = await self.repo.get_by_id(session_id)
        if session is None:
            return
        try:
            session.mark_paid(daily_pass_id)
        except (d.SessionAlreadyPaid, d.InvalidStateTransition):
            return

        await self.repo.mark_paid(session_id, daily_pass_id)
        await self.bus.publish(SessionPaid(
            session_id=session_id,
            host_client_id=session.host_client_id,
            daily_pass_id=daily_pass_id,
        ))

    async def send_request(
        self,
        requester_client_id: int,
        session_id: int,
        message: Optional[str] = None,
    ) -> dto.SessionRequestDTO:
        session = await self.repo.get_by_id(session_id)
        if session is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Session not found",
                error_code="GYMMATE_SESSION_NOT_FOUND",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )

        existing = await self.req_repo.get_by_session_and_requester(
            session_id, requester_client_id
        )
        if existing is not None and existing.status == d.RequestStatus.PENDING:
            return self._to_request_dto(existing)
        if existing is not None and existing.status == d.RequestStatus.ACCEPTED:
            raise FittbotHTTPException(
                status_code=400,
                detail="You are already a member of this session",
                error_code="GYMMATE_REQUEST_ALREADY_ACCEPTED",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )
        if existing is not None and existing.status == d.RequestStatus.REJECTED:
            # The host rejected this requester before — don't let them
            # re-send to the same session. Sticky decision on the host's part.
            raise FittbotHTTPException(
                status_code=400,
                detail="Your previous request to this session was declined",
                error_code="GYMMATE_REQUEST_PREVIOUSLY_REJECTED",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )

        try:
            request = d.SessionRequest.create(
                session=session,
                requester_client_id=requester_client_id,
                message=message,
            )
        except (
            d.CannotRequestOwnSession,
            d.SessionNotJoinable,
            d.InvalidRequestMessage,
        ) as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_REQUEST_INVALID",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )

        await self.req_repo.add(request)
        await self.bus.publish(SessionRequestCreated(
            request_id=request.id,
            session_id=session_id,
            host_client_id=session.host_client_id,
            requester_client_id=requester_client_id,
        ))
        await self._fire(session.host_client_id, requester_client_id)
        return self._to_request_dto(request)

    async def accept_request(
        self, host_client_id: int, request_id: int
    ) -> None:
        request = await self.req_repo.get_by_id(request_id)
        if request is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Request not found",
                error_code="GYMMATE_REQUEST_NOT_FOUND",
                log_data={"client_id": host_client_id, "request_id": request_id},
            )
        try:
            request.accept(host_client_id)
        except d.SessionNotOwned as exc:
            raise FittbotHTTPException(
                status_code=403,
                detail=str(exc),
                error_code="GYMMATE_REQUEST_FORBIDDEN",
                log_data={"client_id": host_client_id, "request_id": request_id},
            )
        except d.RequestNotPending as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_REQUEST_BAD_STATE",
                log_data={"client_id": host_client_id, "request_id": request_id},
            )

        await self.req_repo.update_status(
            request_id, request.status.value, request.responded_at
        )
        await self.req_repo.add_member(
            request.session_id, request.requester_client_id, role="member"
        )
        await self.bus.publish(SessionRequestAccepted(
            request_id=request_id,
            session_id=request.session_id,
            host_client_id=host_client_id,
            requester_client_id=request.requester_client_id,
        ))
        await self._fire(host_client_id, request.requester_client_id)

    async def reject_request(
        self, host_client_id: int, request_id: int
    ) -> None:
        request = await self.req_repo.get_by_id(request_id)
        if request is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Request not found",
                error_code="GYMMATE_REQUEST_NOT_FOUND",
                log_data={"client_id": host_client_id, "request_id": request_id},
            )
        try:
            request.reject(host_client_id)
        except d.SessionNotOwned as exc:
            raise FittbotHTTPException(
                status_code=403,
                detail=str(exc),
                error_code="GYMMATE_REQUEST_FORBIDDEN",
                log_data={"client_id": host_client_id, "request_id": request_id},
            )
        except d.RequestNotPending as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_REQUEST_BAD_STATE",
                log_data={"client_id": host_client_id, "request_id": request_id},
            )

        await self.req_repo.update_status(
            request_id, request.status.value, request.responded_at
        )
        await self.bus.publish(SessionRequestRejected(
            request_id=request_id,
            session_id=request.session_id,
            host_client_id=host_client_id,
            requester_client_id=request.requester_client_id,
        ))
        await self._fire(host_client_id, request.requester_client_id)

    async def withdraw_request(
        self, requester_client_id: int, request_id: int
    ) -> None:
        request = await self.req_repo.get_by_id(request_id)
        if request is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Request not found",
                error_code="GYMMATE_REQUEST_NOT_FOUND",
                log_data={"client_id": requester_client_id, "request_id": request_id},
            )
        try:
            request.withdraw(requester_client_id)
        except d.RequestNotOwned as exc:
            raise FittbotHTTPException(
                status_code=403,
                detail=str(exc),
                error_code="GYMMATE_REQUEST_FORBIDDEN",
                log_data={"client_id": requester_client_id, "request_id": request_id},
            )
        except d.RequestNotPending as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_REQUEST_BAD_STATE",
                log_data={"client_id": requester_client_id, "request_id": request_id},
            )

        await self.req_repo.update_status(
            request_id, request.status.value, request.responded_at
        )
        await self.bus.publish(SessionRequestWithdrawn(
            request_id=request_id,
            session_id=request.session_id,
            host_client_id=request.host_client_id,
            requester_client_id=requester_client_id,
        ))
        await self._fire(request.host_client_id, requester_client_id)

    async def list_session_participants(
        self, viewer_client_id: int, session_id: int
    ) -> List[dto.SessionParticipantDTO]:
        """All accepted members of a session for the participants modal.
        Any authenticated viewer can fetch this — it's the same discovery
        info that powers the session cards.
        """
        session = await self.repo.get_by_id(session_id)
        if session is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Session not found",
                error_code="GYMMATE_SESSION_NOT_FOUND",
                log_data={"client_id": viewer_client_id, "session_id": session_id},
            )
        rows = await self.req_repo.list_session_members(session_id)
        return [
            dto.SessionParticipantDTO(
                client_id=r["client_id"],
                name=r["name"],
                avatar_url=_avatar_url_or_none(r["avatar_url"]),
                role=r["role"],
                is_viewer=(r["client_id"] == viewer_client_id),
                joined_at=r["joined_at"],
            )
            for r in rows
        ]

    async def list_pending_for_session(
        self, requester_client_id: int, session_id: int
    ) -> List[dto.PendingRequestDTO]:
        session = await self.repo.get_by_id(session_id)
        if session is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Session not found",
                error_code="GYMMATE_SESSION_NOT_FOUND",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )
        if session.host_client_id != requester_client_id:
            raise FittbotHTTPException(
                status_code=403,
                detail="Only the host can see pending requests",
                error_code="GYMMATE_SESSION_FORBIDDEN",
                log_data={"client_id": requester_client_id, "session_id": session_id},
            )
        rows = await self.req_repo.list_pending_for_session(session_id)
        return [
            dto.PendingRequestDTO(
                request_id=r["request_id"],
                session_id=session_id,
                requester_client_id=r["requester_client_id"],
                requester_name=r["requester_name"],
                requester_avatar_url=_avatar_url_or_none(r["requester_avatar_url"]),
                message=r["message"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def list_inbox(self, host_client_id: int) -> List[dto.PendingRequestDTO]:
        rows = await self.req_repo.list_pending_for_host(host_client_id)
        return [
            dto.PendingRequestDTO(
                request_id=r["request_id"],
                session_id=r["session_id"],
                requester_client_id=r["requester_client_id"],
                requester_name=r["requester_name"],
                requester_avatar_url=_avatar_url_or_none(r["requester_avatar_url"]),
                message=r["message"],
                session_date=r["session_date"],
                session_time=r["session_time"],
                gym_id=r.get("gym_id"),
                gym_name=r.get("gym_name"),
                gym_area=r.get("gym_area"),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def list_sent(self, client_id: int) -> List[dto.SentRequestDTO]:
        """Requests this client SENT to other people's sessions (pending only,
        non-expired)."""
        rows = await self.req_repo.list_sent_requests_for_client(client_id)
        for r in rows:
            r["host_avatar_url"] = _avatar_url_or_none(r.get("host_avatar_url"))
        return [dto.SentRequestDTO(**r) for r in rows]

    async def list_hosted_sessions(
        self, host_client_id: int,
    ) -> List[dto.HostedSessionDTO]:
        """The viewer's own open future sessions with create-time
        details + gym info + joiner count. Sorted by nearest first.

        Uses the same shared `fetch_gym_info` helper as matches/nearby
        — single source of truth for gym name/area/cover_pic/price.
        """
        rows = await self.req_repo.list_hosted_sessions_for_client(host_client_id)
        if not rows:
            return []

        gym_ids = list({r["gym_id"] for r in rows})
        gym_info_map = {}
        if self.db is not None and self.redis is not None:
            from app.fittbot_api.v2.Fymble.fitness_studios.shared.gym_price_enricher import (
                fetch_gym_info,
            )
            gym_info_map = await fetch_gym_info(self.db, self.redis, gym_ids)

        out: List[dto.HostedSessionDTO] = []
        for r in rows:
            gi = gym_info_map.get(r["gym_id"])
            gym_dto = (
                dto.MatchedSessionGymDTO(
                    gym_id=gi.gym_id,
                    name=gi.name,
                    area=gi.area,
                    cover_pic=gi.cover_pic,
                    dailypass_price=gi.dailypass_price,
                )
                if gi is not None
                else dto.MatchedSessionGymDTO(gym_id=r["gym_id"])
            )
            out.append(dto.HostedSessionDTO(
                session_id=r["session_id"],
                session_date=r["session_date"],
                session_time=r["session_time"],
                gym=gym_dto,
                mate_preference=r["mate_preference"],
                fitness_level=r["fitness_level"],
                workout_vibes=r["workout_vibes"],
                payment_mode=r["payment_mode"],
                payment_status=r["payment_status"],
                status=r["status"],
                joiner_count=r["joiner_count"],
                created_at=r["created_at"],
            ))
        return out

    async def list_my_matches(
        self, client_id: int,
    ) -> List[dto.MatchedSessionDTO]:
        """One entry per open future session the client is in, with:
            - gym info (name, area, cover_pic, dailypass_price)
            - every accepted member of that session (viewer marked with
              is_viewer=true)
        Sorted by nearest session date/time first.

        Pricing/gym info uses the shared `fetch_gym_info` helper so the
        same logic that powers `nearby_gyms` powers this too.
        """
        grouped = await self.req_repo.list_matched_sessions_for_client(client_id)
        if not grouped:
            return []

        # Bidirectional block filter — drop members the viewer is blocked
        # with from each session's member list. If only the viewer (or
        # nothing) remains, the session disappears from matches entirely.
        blocked_ids: set = set()
        if self.db is not None:
            from app.fittbot_api.v2.Fymble.gym_mate.blocks._repository import (
                BlockRepository,
            )
            blocked_ids = await BlockRepository(self.db).get_bidirectional_block_ids(
                client_id,
            )
        if blocked_ids:
            filtered = []
            for s in grouped:
                kept = [m for m in s["members"] if m["client_id"] not in blocked_ids]
                # Session needs at least the viewer + one other un-blocked
                # member to be a real "match" — otherwise drop it.
                if len(kept) >= 2:
                    s = {**s, "members": kept}
                    filtered.append(s)
            grouped = filtered
        if not grouped:
            return []

        # Enrich gyms in a single batch (parallel fetch + price compute).
        gym_ids = list({s["gym_id"] for s in grouped})
        gym_info_map = {}
        if self.db is not None and self.redis is not None:
            from app.fittbot_api.v2.Fymble.fitness_studios.shared.gym_price_enricher import (
                fetch_gym_info,
            )
            gym_info_map = await fetch_gym_info(self.db, self.redis, gym_ids)

        out: List[dto.MatchedSessionDTO] = []
        for s in grouped:
            gi = gym_info_map.get(s["gym_id"])
            gym_dto = (
                dto.MatchedSessionGymDTO(
                    gym_id=gi.gym_id,
                    name=gi.name,
                    area=gi.area,
                    cover_pic=gi.cover_pic,
                    dailypass_price=gi.dailypass_price,
                )
                if gi is not None
                else dto.MatchedSessionGymDTO(gym_id=s["gym_id"])
            )
            members = [
                dto.MatchedSessionMemberDTO(
                    client_id=m["client_id"],
                    name=m["name"],
                    avatar_url=_avatar_url_or_none(m["avatar_url"]),
                    is_viewer=(m["client_id"] == client_id),
                )
                for m in s["members"]
            ]
            out.append(dto.MatchedSessionDTO(
                session_id=s["session_id"],
                session_date=s["session_date"],
                session_time=s["session_time"],
                gym=gym_dto,
                members=members,
            ))
        return out

    async def list_all_nearby_gym_mates(
        self,
        viewer_client_id: int,
        distance_map: Dict[int, float],
        limit: int = 100,
        viewer_gender: Optional[str] = None,
    ) -> List[dto.NearbyGymMateAllDTO]:
        """View-all variant of nearby_gym_mates — returns all matching
        sessions in the supplied distance_map (within the 30km GEOSEARCH
        radius the caller already applied) with the viewer's pending
        request status attached to each.

        The home embed (`list_nearby_gym_mates`) is intentionally a
        separate code path and stays slim (no status column, capped at 10).
        """
        if not distance_map:
            return []
        if viewer_gender is None:
            viewer_gender = await self.req_repo.get_client_gender(viewer_client_id)
        if viewer_gender:
            viewer_gender = viewer_gender.strip().lower()

        gym_ids = list(distance_map.keys())
        rows = await self.req_repo.list_all_sessions_at_gyms_with_status(
            gym_ids=gym_ids,
            viewer_client_id=viewer_client_id,
            viewer_gender=viewer_gender,
            limit=max(limit * 3, 200),
        )
        enriched = []
        for r in rows:
            dist = distance_map.get(r["gym_id"])
            if dist is None:
                continue
            enriched.append((dist, r))
        enriched.sort(key=lambda kv: (kv[0], kv[1]["session_date"], kv[1]["session_time"]))

        out: List[dto.NearbyGymMateAllDTO] = []
        for i, (dist, r) in enumerate(enriched[:limit], start=1):
            pending_id = r.get("pending_request_id")
            out.append(dto.NearbyGymMateAllDTO(
                sno=i,
                session_id=r["session_id"],
                host_client_id=r["host_client_id"],
                host_name=r["host_name"],
                host_avatar_url=_avatar_url_or_none(r["host_avatar_url"]),
                host_bio=r.get("host_bio"),
                gym_id=r["gym_id"],
                gym_name=r["gym_name"],
                gym_area=r.get("gym_area"),
                distance_km=round(dist, 2),
                session_date=r["session_date"],
                session_time=r["session_time"],
                mate_preference=r["mate_preference"],
                fitness_level=r["fitness_level"],
                workout_vibes=r.get("workout_vibes") or [],
                payment_mode=r["payment_mode"],
                dailypass_booked=bool(r.get("dailypass_booked", False)),
                request_status="pending" if pending_id else "none",
                pending_request_id=pending_id,
            ))
        return out

    async def list_nearby_gym_mates(
        self,
        viewer_client_id: int,
        distance_map: Dict[int, float],
        limit: int = 20,
        viewer_gender: Optional[str] = None,
    ) -> List[dto.NearbyGymMateDTO]:
        """Sessions in nearby gyms with distance attached.

        `distance_map` is `{gym_id: distance_km}` returned by the geo
        service (already radius-filtered + sorted by distance). Gender
        filtering is applied at the SQL layer so we don't pull rows we
        can't show. If `viewer_gender` is omitted, it's resolved via
        `get_client_gender` so the SQL filter always has the truth.
        """
        if not distance_map:
            return []
        if viewer_gender is None:
            viewer_gender = await self.req_repo.get_client_gender(viewer_client_id)
        # `clients.gender` is freeform user data ("male", "Male", " Female ")
        # while `mate_preference` is title-case ("Male", "Female"). Lower-case
        # both sides at comparison time so the IN-list actually matches.
        if viewer_gender:
            viewer_gender = viewer_gender.strip().lower()

        gym_ids = list(distance_map.keys())
        rows = await self.req_repo.list_sessions_at_gyms(
            gym_ids=gym_ids,
            viewer_client_id=viewer_client_id,
            viewer_gender=viewer_gender,
            limit=max(limit * 5, 100),
        )
        enriched = []
        for r in rows:
            dist = distance_map.get(r["gym_id"])
            if dist is None:
                continue
            enriched.append((dist, r))
        enriched.sort(key=lambda kv: (kv[0], kv[1]["session_date"], kv[1]["session_time"]))
        return [
            dto.NearbyGymMateDTO(
                sno=i,
                session_id=r["session_id"],
                host_client_id=r["host_client_id"],
                host_name=r["host_name"],
                host_avatar_url=_avatar_url_or_none(r["host_avatar_url"]),
                gym_id=r["gym_id"],
                gym_name=r["gym_name"],
                gym_area=r.get("gym_area"),
                distance_km=round(dist, 2),
                session_date=r["session_date"],
                session_time=r["session_time"],
                dailypass_booked=bool(r.get("dailypass_booked", False)),
            )
            for i, (dist, r) in enumerate(enriched[:limit], start=1)
        ]

    async def get_host_summary(
        self,
        host_client_id: int,
        host_identity: Optional[dto.HostIdentityDTO] = None,
    ) -> dto.HostSessionsSummaryDTO:
        """Composite summary for the home payload.

        `host_identity` is an optimization for callers (HomeService) that
        already loaded the client's name + avatar from another query, so
        we don't hit `clients` twice for the same row. When omitted,
        falls back to a single `get_client_basic` lookup.
        """
        if host_identity is None:
            row = await self.req_repo.get_client_basic(host_client_id)
            if row:
                row["avatar_url"] = _avatar_url_or_none(row.get("avatar_url"))
                host_identity = dto.HostIdentityDTO(**row)
            else:
                host_identity = dto.HostIdentityDTO(client_id=host_client_id)

        future_count = await self.req_repo.count_future_sessions_for_host(host_client_id)

        # `received_requests` and `recent_avatars` only make sense for
        # hosted sessions — skip those two queries when nothing hosted.
        # `match` is independent: the viewer might be a member of someone
        # else's session even with zero hosted, so it's always queried.
        if future_count > 0:
            pending_count = await self.req_repo.count_pending_for_host(host_client_id)
            avatars = await self.req_repo.recent_requester_avatars_for_host(
                host_client_id, limit=3
            )
            avatars = [_avatar_url_or_none(a) for a in avatars if a]
            avatars = [a for a in avatars if a]
            received_requests = (
                dto.ReceivedRequestsSummaryDTO(
                    pending_count=pending_count, recent_avatars=avatars,
                )
                if pending_count > 0 else None
            )
        else:
            received_requests = None

        match_row = await self.req_repo.get_latest_match(host_client_id)
        if match_row:
            match_row["avatar_url"] = _avatar_url_or_none(match_row.get("avatar_url"))

        return dto.HostSessionsSummaryDTO(
            host=host_identity,
            future_count=future_count if future_count > 0 else None,
            received_requests=received_requests,
            match=dto.MatchDTO(**match_row) if match_row else None,
        )

    @staticmethod
    def _to_dto(session: d.Session) -> dto.SessionDTO:
        return dto.SessionDTO(
            session_id=session.id,
            host_client_id=session.host_client_id,
            gym_id=session.gym_id,
            session_date=session.session_date,
            session_time=session.session_time,
            mate_preference=session.mate_preference.value,
            fitness_level=session.fitness_level.value,
            workout_vibes=session.workout_vibes.as_list(),
            payment_mode=session.payment_mode.value,
            payment_status=session.payment_status.value,
            daily_pass_id=session.daily_pass_id,
            status=session.status.value,
        )

    @staticmethod
    def _to_request_dto(request: d.SessionRequest) -> dto.SessionRequestDTO:
        return dto.SessionRequestDTO(
            request_id=request.id,
            session_id=request.session_id,
            requester_client_id=request.requester_client_id,
            host_client_id=request.host_client_id,
            message=request.message.value if request.message else None,
            status=request.status.value,
            created_at=request.created_at,
            responded_at=request.responded_at,
        )
