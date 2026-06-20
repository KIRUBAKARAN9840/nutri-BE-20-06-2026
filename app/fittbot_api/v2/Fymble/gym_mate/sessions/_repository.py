from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.fittbot_models.client import Client as ClientORM
from app.models.fittbot_models.gym import Gym as GymORM
from app.models.fittbot_models.gymmate import (
    GymMateBlock as BlockORM,
    GymMateProfile as GymMateProfileORM,
    GymMateProfilePhoto as GymMatePhotoORM,
    GymMateSession as SessionORM,
    GymMateSessionMember as SessionMemberORM,
    GymMateSessionRequest as SessionRequestORM,
)

from . import _domain as d


# IST is UTC+5h30m. Production MySQL runs in UTC, but session_date /
# session_time are stored as IST wall-clock values (user-visible
# "10:00 AM" stays "10:00 AM" in the DB without TZ conversion). To
# decide "has this session started yet", we compare the stored
# (date+time) against IST-now = UTC_TIMESTAMP() + 5h30m.
#
# Used by every discovery / inbox / sent / count path so they all
# disappear the moment IST wall-clock passes session_time. Chat has
# a separate rule (visible till end of session_date) and uses its
# own filter in chat/_repository.py.
_IST_OFFSET_MINUTES = 330


def _session_not_started_yet():
    """SQL fragment: session has not yet started in IST."""
    return func.timestamp(
        SessionORM.session_date, SessionORM.session_time,
    ) > func.utc_timestamp() + text(f"INTERVAL {_IST_OFFSET_MINUTES} MINUTE")


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, session: d.Session) -> d.Session:
        row = SessionORM(
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
            razorpay_order_id=session.razorpay_order_id,
            status=session.status.value,
        )
        self.db.add(row)
        await self.db.flush()
        session.id = row.id
        return session

    async def get_by_id(self, session_id: int) -> Optional[d.Session]:
        row = (await self.db.execute(
            select(SessionORM).where(SessionORM.id == session_id)
        )).scalar_one_or_none()
        if row is None:
            return None
        return self._to_domain(row)

    async def update_status(self, session_id: int, status: str) -> None:
        await self.db.execute(
            update(SessionORM).where(SessionORM.id == session_id).values(status=status)
        )

    async def mark_paid(self, session_id: int, daily_pass_id: str) -> None:
        await self.db.execute(
            update(SessionORM)
            .where(SessionORM.id == session_id)
            .values(payment_status="paid", daily_pass_id=daily_pass_id)
        )

    async def set_razorpay_order(self, session_id: int, razorpay_order_id: str) -> None:
        await self.db.execute(
            update(SessionORM)
            .where(SessionORM.id == session_id)
            .values(razorpay_order_id=razorpay_order_id)
        )

    @staticmethod
    def _to_domain(row: SessionORM) -> d.Session:
        return d.Session(
            id=row.id,
            host_client_id=row.host_client_id,
            gym_id=row.gym_id,
            session_date=row.session_date,
            session_time=row.session_time,
            mate_preference=d.MatePreference(row.mate_preference),
            fitness_level=d.FitnessLevel(row.fitness_level),
            workout_vibes=d.WorkoutVibes(tuple(row.workout_vibes)),
            payment_mode=d.PaymentMode(row.payment_mode),
            payment_status=d.PaymentStatus(row.payment_status),
            daily_pass_id=row.daily_pass_id,
            razorpay_order_id=row.razorpay_order_id,
            status=d.SessionStatus(row.status),
        )


class SessionRequestRepository:
    """Reads and writes for session_request + session_member.

    Kept separate from SessionRepository because join requests and
    matches have a fan-out lifecycle independent of the Session
    aggregate itself, and most queries here JOIN to clients/sessions
    rather than load a single Session aggregate.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, request: d.SessionRequest) -> d.SessionRequest:
        """Insert a pending request, OR reopen a withdrawn one back to
        pending (UNIQUE on session_id+requester would otherwise reject
        a fresh INSERT). Caller is responsible for blocking the
        REJECTED/ACCEPTED transitions before reaching here."""
        stmt = (
            mysql_insert(SessionRequestORM)
            .values(
                session_id=request.session_id,
                requester_client_id=request.requester_client_id,
                host_client_id=request.host_client_id,
                message=request.message.value if request.message else None,
                status="pending",
                created_at=datetime.now(),
                responded_at=None,
            )
        )
        stmt = stmt.on_duplicate_key_update(
            status="pending",
            created_at=datetime.now(),
            responded_at=None,
            message=request.message.value if request.message else None,
        )
        await self.db.execute(stmt)
        # Re-read so the caller gets the canonical id + created_at.
        row = (await self.db.execute(
            select(SessionRequestORM).where(
                SessionRequestORM.session_id == request.session_id,
                SessionRequestORM.requester_client_id == request.requester_client_id,
            )
        )).scalar_one_or_none()
        if row is not None:
            request.id = row.id
            request.created_at = row.created_at
        return request

    async def get_by_id(self, request_id: int) -> Optional[d.SessionRequest]:
        row = (await self.db.execute(
            select(SessionRequestORM).where(SessionRequestORM.id == request_id)
        )).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_session_and_requester(
        self, session_id: int, requester_client_id: int
    ) -> Optional[d.SessionRequest]:
        row = (await self.db.execute(
            select(SessionRequestORM).where(
                (SessionRequestORM.session_id == session_id)
                & (SessionRequestORM.requester_client_id == requester_client_id)
            )
        )).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update_status(
        self,
        request_id: int,
        status: str,
        responded_at: Optional[datetime],
    ) -> None:
        await self.db.execute(
            update(SessionRequestORM)
            .where(SessionRequestORM.id == request_id)
            .values(status=status, responded_at=responded_at)
        )

    async def add_member(
        self, session_id: int, client_id: int, role: str = "member"
    ) -> None:
        """INSERT IGNORE into session_member so accept is idempotent."""
        stmt = mysql_insert(SessionMemberORM).values(
            session_id=session_id,
            client_id=client_id,
            role=role,
            joined_at=datetime.now(),
        )
        stmt = stmt.prefix_with("IGNORE")
        await self.db.execute(stmt)

    async def count_pending_for_host(self, host_client_id: int) -> int:
        """Count pending requests for sessions hosted by this client,
        restricted to today-or-future sessions."""
        stmt = (
            select(func.count(SessionRequestORM.id))
            .select_from(SessionRequestORM)
            .join(SessionORM, SessionORM.id == SessionRequestORM.session_id)
            .where(
                (SessionRequestORM.host_client_id == host_client_id)
                & (SessionRequestORM.status == "pending")
                & (SessionORM.status == "open")
                & _session_not_started_yet()
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def recent_requester_avatars_for_host(
        self, host_client_id: int, limit: int = 3
    ) -> List[str]:
        """Most recent pending requesters' avatar URLs across all of the
        host's open future sessions. De-duped by requester so the same
        person isn't shown twice.

        Avatar precedence mirrors matches / nearby / chat:
            1) gym_mate.profile_photo (is_primary=True)
            2) clients.profile (fallback)
        Rows where both are NULL are dropped so the response never
        contains nulls."""
        latest_per_requester = (
            select(
                SessionRequestORM.requester_client_id.label("requester_id"),
                func.max(SessionRequestORM.created_at).label("latest_at"),
            )
            .select_from(SessionRequestORM)
            .join(SessionORM, SessionORM.id == SessionRequestORM.session_id)
            .where(
                (SessionRequestORM.host_client_id == host_client_id)
                & (SessionRequestORM.status == "pending")
                & (SessionORM.status == "open")
                & _session_not_started_yet()
            )
            .group_by(SessionRequestORM.requester_client_id)
            .subquery()
        )

        avatar_expr = func.coalesce(
            GymMatePhotoORM.s3_path, ClientORM.profile,
        ).label("avatar")
        stmt = (
            select(avatar_expr)
            .select_from(latest_per_requester)
            .join(
                ClientORM,
                ClientORM.client_id == latest_per_requester.c.requester_id,
            )
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == latest_per_requester.c.requester_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(avatar_expr.isnot(None))
            .order_by(latest_per_requester.c.latest_at.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [r.avatar for r in rows if r.avatar]

    async def list_pending_for_host(
        self, host_client_id: int, limit: int = 50, offset: int = 0
    ) -> List[dict]:
        """Inbox view: pending requests across all of the host's open
        future sessions with requester display info + gym info."""
        avatar_expr = func.coalesce(
            GymMatePhotoORM.s3_path, ClientORM.profile,
        ).label("avatar")
        stmt = (
            select(
                SessionRequestORM.id.label("request_id"),
                SessionRequestORM.session_id,
                SessionRequestORM.requester_client_id,
                SessionRequestORM.message,
                SessionRequestORM.created_at,
                SessionORM.session_date,
                SessionORM.session_time,
                SessionORM.gym_id,
                ClientORM.name,
                avatar_expr,
                GymORM.name.label("gym_name"),
                GymORM.area.label("gym_area"),
                GymORM.city.label("gym_city"),
            )
            .select_from(SessionRequestORM)
            .join(SessionORM, SessionORM.id == SessionRequestORM.session_id)
            .join(
                ClientORM,
                ClientORM.client_id == SessionRequestORM.requester_client_id,
            )
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == ClientORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .join(GymORM, GymORM.gym_id == SessionORM.gym_id)
            .where(
                (SessionRequestORM.host_client_id == host_client_id)
                & (SessionRequestORM.status == "pending")
                & (SessionORM.status == "open")
                & _session_not_started_yet()
            )
            .order_by(SessionRequestORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "request_id": r.request_id,
                "session_id": r.session_id,
                "requester_client_id": r.requester_client_id,
                "requester_name": r.name,
                "requester_avatar_url": r.avatar,
                "message": r.message,
                "session_date": r.session_date,
                "session_time": r.session_time,
                "gym_id": r.gym_id,
                "gym_name": r.gym_name,
                "gym_area": (r.gym_area or r.gym_city) or None,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def list_pending_for_session(
        self, session_id: int, limit: int = 50
    ) -> List[dict]:
        avatar_expr = func.coalesce(
            GymMatePhotoORM.s3_path, ClientORM.profile,
        ).label("avatar")
        stmt = (
            select(
                SessionRequestORM.id.label("request_id"),
                SessionRequestORM.requester_client_id,
                SessionRequestORM.message,
                SessionRequestORM.created_at,
                ClientORM.name,
                avatar_expr,
            )
            .select_from(SessionRequestORM)
            .join(
                ClientORM,
                ClientORM.client_id == SessionRequestORM.requester_client_id,
            )
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == ClientORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                (SessionRequestORM.session_id == session_id)
                & (SessionRequestORM.status == "pending")
            )
            .order_by(SessionRequestORM.created_at.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "request_id": r.request_id,
                "requester_client_id": r.requester_client_id,
                "requester_name": r.name,
                "requester_avatar_url": r.avatar,
                "message": r.message,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def get_latest_match(self, client_id: int) -> Optional[dict]:
        """Latest match across every open future session the viewer is
        part of -- whether they hosted it or joined someone else's.

        Returns the OTHER party in that session (host sees the latest
        joiner; joiner sees the host they matched with). Most-recently-
        created session wins; within it, the latest accepted member.
        None when the viewer isn't in any future open session, or is
        the sole member of every one they're in."""
        ViewerMember = aliased(SessionMemberORM)
        Other = aliased(SessionMemberORM)

        stmt = (
            select(
                Other.client_id,
                Other.session_id,
                SessionORM.session_date,
                ClientORM.name,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(ViewerMember)
            .join(SessionORM, SessionORM.id == ViewerMember.session_id)
            .join(
                Other,
                and_(
                    Other.session_id == ViewerMember.session_id,
                    Other.client_id != ViewerMember.client_id,
                ),
            )
            .join(ClientORM, ClientORM.client_id == Other.client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == Other.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                (ViewerMember.client_id == client_id)
                & (SessionORM.status == "open")
                & _session_not_started_yet()
                # Skip the other party if they're in a block pair with
                # the viewer — the "match" should not surface a blocked
                # person on the home card.
                & ~select(BlockORM.id).where(
                    or_(
                        and_(
                            BlockORM.blocker_client_id == client_id,
                            BlockORM.blocked_client_id == Other.client_id,
                        ),
                        and_(
                            BlockORM.blocker_client_id == Other.client_id,
                            BlockORM.blocked_client_id == client_id,
                        ),
                    )
                ).exists()
            )
            .order_by(
                SessionORM.created_at.desc(),
                # When the viewer is a joiner (not the session host),
                # surface the session host as the "match" they paired
                # with. When the viewer IS the host, this CASE is 0 for
                # every Other, so we fall through to the latest-joiner
                # tiebreaker below.
                case(
                    (
                        and_(
                            ViewerMember.client_id != SessionORM.host_client_id,
                            Other.client_id == SessionORM.host_client_id,
                        ),
                        1,
                    ),
                    else_=0,
                ).desc(),
                Other.joined_at.desc(),
            )
            .limit(1)
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None
        return {
            "client_id": row.client_id,
            "name": row.name,
            "avatar_url": row.avatar,
            "session_id": row.session_id,
            "session_date": row.session_date,
        }

    async def list_sent_requests_for_client(
        self,
        client_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict]:
        """Pending requests this client SENT to other people's sessions.
        Only sessions that are still open AND not past their date.
        Includes gym name + area for the destination session."""
        host_avatar_expr = func.coalesce(
            GymMatePhotoORM.s3_path, ClientORM.profile,
        ).label("host_avatar")
        stmt = (
            select(
                SessionRequestORM.id.label("request_id"),
                SessionRequestORM.session_id,
                SessionRequestORM.message,
                SessionRequestORM.created_at,
                SessionORM.session_date,
                SessionORM.session_time,
                SessionORM.gym_id,
                SessionORM.host_client_id,
                ClientORM.name.label("host_name"),
                host_avatar_expr,
                GymORM.name.label("gym_name"),
                GymORM.area.label("gym_area"),
                GymORM.city.label("gym_city"),
            )
            .select_from(SessionRequestORM)
            .join(SessionORM, SessionORM.id == SessionRequestORM.session_id)
            .join(ClientORM, ClientORM.client_id == SessionORM.host_client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == ClientORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .join(GymORM, GymORM.gym_id == SessionORM.gym_id)
            .where(
                SessionRequestORM.requester_client_id == client_id,
                SessionRequestORM.status == "pending",
                SessionORM.status == "open",
                _session_not_started_yet(),
            )
            .order_by(SessionRequestORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "request_id": r.request_id,
                "session_id": r.session_id,
                "session_date": r.session_date,
                "session_time": r.session_time,
                "host_client_id": r.host_client_id,
                "host_name": r.host_name,
                "host_avatar_url": r.host_avatar,
                "gym_id": r.gym_id,
                "gym_name": r.gym_name,
                "gym_area": (r.gym_area or r.gym_city) or None,
                "message": r.message,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def list_hosted_sessions_for_client(
        self,
        host_client_id: int,
        limit: int = 50,
    ) -> List[dict]:
        """The viewer's OWN open future sessions with all create-time
        details + the joiner count.

        joiner_count = members other than the host. Lets the frontend
        decide whether to flag a session as "no joiners yet" without
        loading the full member list.
        """
        # Subquery: members other than the host per session.
        joiner_count_subq = (
            select(func.count(SessionMemberORM.id))
            .where(
                SessionMemberORM.session_id == SessionORM.id,
                SessionMemberORM.client_id != host_client_id,
            )
            .correlate(SessionORM)
            .scalar_subquery()
        )

        stmt = (
            select(
                SessionORM.id.label("session_id"),
                SessionORM.session_date,
                SessionORM.session_time,
                SessionORM.gym_id,
                SessionORM.mate_preference,
                SessionORM.fitness_level,
                SessionORM.workout_vibes,
                SessionORM.payment_mode,
                SessionORM.payment_status,
                SessionORM.status,
                SessionORM.created_at,
                joiner_count_subq.label("joiner_count"),
            )
            .where(
                SessionORM.host_client_id == host_client_id,
                SessionORM.status == "open",
                _session_not_started_yet(),
            )
            .order_by(
                SessionORM.session_date.asc(),
                SessionORM.session_time.asc(),
            )
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "session_id": r.session_id,
                "session_date": r.session_date,
                "session_time": r.session_time,
                "gym_id": r.gym_id,
                "mate_preference": r.mate_preference,
                "fitness_level": r.fitness_level,
                "workout_vibes": list(r.workout_vibes or []),
                "payment_mode": r.payment_mode,
                "payment_status": r.payment_status,
                "status": r.status,
                "joiner_count": int(r.joiner_count or 0),
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def list_matched_sessions_for_client(
        self,
        client_id: int,
        limit: int = 50,
    ) -> List[dict]:
        """Session-grouped matches. One entry per open future session the
        client is a member of, with EVERY accepted member (including the
        viewer themself) attached.

        Output shape:
            [{
              session_id, session_date, session_time, gym_id,
              members: [{client_id, name, avatar_url}, ...]
            }, ...]

        Sorted by soonest session (date ASC, time ASC). Sessions with
        only the viewer (no other matches yet) are dropped — only true
        matches surface here.
        """
        ViewerMember = aliased(SessionMemberORM)
        AllMembers = aliased(SessionMemberORM)

        stmt = (
            select(
                SessionORM.id.label("session_id"),
                SessionORM.session_date,
                SessionORM.session_time,
                SessionORM.gym_id,
                AllMembers.client_id.label("member_id"),
                AllMembers.joined_at,
                ClientORM.name,
                ClientORM.profile,
                GymMatePhotoORM.s3_path.label("dp"),
            )
            .select_from(SessionORM)
            .join(
                ViewerMember,
                and_(
                    ViewerMember.session_id == SessionORM.id,
                    ViewerMember.client_id == client_id,
                ),
            )
            .join(AllMembers, AllMembers.session_id == SessionORM.id)
            .join(ClientORM, ClientORM.client_id == AllMembers.client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == AllMembers.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                SessionORM.status == "open",
                _session_not_started_yet(),
            )
            .order_by(
                SessionORM.session_date.asc(),
                SessionORM.session_time.asc(),
                AllMembers.joined_at.asc(),
            )
        )
        rows = (await self.db.execute(stmt)).all()

        sessions: dict = {}
        for r in rows:
            sess = sessions.setdefault(r.session_id, {
                "session_id": r.session_id,
                "session_date": r.session_date,
                "session_time": r.session_time,
                "gym_id": r.gym_id,
                "members": [],
                "_member_ids": set(),
            })
            # session_member is UNIQUE per (session_id, client_id) but
            # the join can in theory duplicate if a client has multiple
            # photos — defensive dedupe.
            if r.member_id in sess["_member_ids"]:
                continue
            sess["_member_ids"].add(r.member_id)
            sess["members"].append({
                "client_id": r.member_id,
                "name": r.name,
                "avatar_url": r.dp or r.profile,
            })

        # Only return sessions where the viewer has a match (>= 2 members).
        # Already in date-ASC order from SQL; preserve it.
        result = []
        for s in sessions.values():
            if len(s["members"]) < 2:
                continue
            s.pop("_member_ids", None)
            result.append(s)
            if len(result) >= limit:
                break
        return result

    async def get_client_basic(self, client_id: int) -> Optional[dict]:
        """Lightweight identity lookup for the home payload."""
        stmt = select(
            ClientORM.client_id, ClientORM.name, ClientORM.profile
        ).where(ClientORM.client_id == client_id)
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None
        return {
            "client_id": row.client_id,
            "name": row.name,
            "avatar_url": row.profile,
        }

    async def get_client_gender(self, client_id: int) -> Optional[str]:
        """One-shot lookup of the viewer's gender for gender-aware
        filtering of nearby sessions. PK lookup, ~negligible cost."""
        stmt = select(ClientORM.gender).where(ClientORM.client_id == client_id)
        row = (await self.db.execute(stmt)).first()
        return row.gender if row else None

    async def list_sessions_at_gyms(
        self,
        gym_ids: List[int],
        viewer_client_id: int,
        viewer_gender: Optional[str] = None,
        limit: int = 200,
    ) -> List[dict]:

        if not gym_ids:
            return []

        viewer_gender_norm = (viewer_gender or "").strip().lower()
        if viewer_gender_norm in ("male", "female"):
            allowed_lower = ("group workout", "unisex", "no preference", viewer_gender_norm)
        else:
            allowed_lower = ("group workout", "unisex", "no preference")
        audience_filter = func.lower(SessionORM.mate_preference).in_(allowed_lower)

        rejected_subq = ~select(SessionRequestORM.id).where(
            SessionRequestORM.session_id == SessionORM.id,
            SessionRequestORM.requester_client_id == viewer_client_id,
            SessionRequestORM.status == "rejected",
        ).exists()

        pending_subq = ~select(SessionRequestORM.id).where(
            SessionRequestORM.session_id == SessionORM.id,
            SessionRequestORM.requester_client_id == viewer_client_id,
            SessionRequestORM.status == "pending",
        ).exists()

        already_member_subq = ~select(SessionMemberORM.id).where(
            SessionMemberORM.session_id == SessionORM.id,
            SessionMemberORM.client_id == viewer_client_id,
        ).exists()

        host_not_blocked_subq = ~select(BlockORM.id).where(
            or_(
                and_(
                    BlockORM.blocker_client_id == viewer_client_id,
                    BlockORM.blocked_client_id == SessionORM.host_client_id,
                ),
                and_(
                    BlockORM.blocker_client_id == SessionORM.host_client_id,
                    BlockORM.blocked_client_id == viewer_client_id,
                ),
            )
        ).exists()

        stmt = (
            select(
                SessionORM.id.label("session_id"),
                SessionORM.host_client_id,
                SessionORM.gym_id,
                SessionORM.session_date,
                SessionORM.session_time,
                SessionORM.payment_status,
                ClientORM.name.label("host_name"),
                GymMatePhotoORM.s3_path.label("host_avatar"),
                GymORM.name.label("gym_name"),
                GymORM.area.label("gym_area"),
                GymORM.city.label("gym_city"),
            )
            .select_from(SessionORM)
            .join(ClientORM, ClientORM.client_id == SessionORM.host_client_id)
            .join(GymORM, GymORM.gym_id == SessionORM.gym_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == SessionORM.host_client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                SessionORM.gym_id.in_(gym_ids),
                SessionORM.host_client_id != viewer_client_id,
                SessionORM.status == "open",
                _session_not_started_yet(),
                audience_filter,
                rejected_subq,
                pending_subq,
                already_member_subq,
                host_not_blocked_subq,
            )
            .order_by(SessionORM.session_date.asc(), SessionORM.session_time.asc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "session_id": r.session_id,
                "host_client_id": r.host_client_id,
                "host_name": r.host_name,
                "host_avatar_url": r.host_avatar,
                "gym_id": r.gym_id,
                "gym_name": r.gym_name,
                "gym_area": (r.gym_area or r.gym_city) or None,
                "session_date": r.session_date,
                "session_time": r.session_time,
                "dailypass_booked": r.payment_status == "paid",
            }
            for r in rows
        ]

    async def list_all_sessions_at_gyms_with_status(
        self,
        gym_ids: List[int],
        viewer_client_id: int,
        viewer_gender: Optional[str] = None,
        limit: int = 500,
    ) -> List[dict]:

        if not gym_ids:
            return []
        viewer_gender_norm = (viewer_gender or "").strip().lower()
        if viewer_gender_norm in ("male", "female"):
            allowed_lower = ("group workout", "unisex", "no preference", viewer_gender_norm)
        else:
            allowed_lower = ("group workout", "unisex", "no preference")
        audience_filter = func.lower(SessionORM.mate_preference).in_(allowed_lower)

        rejected_subq = ~select(SessionRequestORM.id).where(
            SessionRequestORM.session_id == SessionORM.id,
            SessionRequestORM.requester_client_id == viewer_client_id,
            SessionRequestORM.status == "rejected",
        ).exists()
        already_member_subq = ~select(SessionMemberORM.id).where(
            SessionMemberORM.session_id == SessionORM.id,
            SessionMemberORM.client_id == viewer_client_id,
        ).exists()
        host_not_blocked_subq = ~select(BlockORM.id).where(
            or_(
                and_(
                    BlockORM.blocker_client_id == viewer_client_id,
                    BlockORM.blocked_client_id == SessionORM.host_client_id,
                ),
                and_(
                    BlockORM.blocker_client_id == SessionORM.host_client_id,
                    BlockORM.blocked_client_id == viewer_client_id,
                ),
            )
        ).exists()

        ViewerPendingReq = aliased(SessionRequestORM)

        stmt = (
            select(
                SessionORM.id.label("session_id"),
                SessionORM.host_client_id,
                SessionORM.gym_id,
                SessionORM.session_date,
                SessionORM.session_time,
                SessionORM.mate_preference,
                SessionORM.fitness_level,
                SessionORM.workout_vibes,
                SessionORM.payment_mode,
                SessionORM.payment_status,
                ClientORM.name.label("host_name"),
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("host_avatar"),
                GymMateProfileORM.bio.label("host_bio"),
                GymORM.name.label("gym_name"),
                GymORM.area.label("gym_area"),
                GymORM.city.label("gym_city"),
                ViewerPendingReq.id.label("viewer_pending_request_id"),
            )
            .select_from(SessionORM)
            .join(ClientORM, ClientORM.client_id == SessionORM.host_client_id)
            .join(GymORM, GymORM.gym_id == SessionORM.gym_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == SessionORM.host_client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .outerjoin(
                ViewerPendingReq,
                and_(
                    ViewerPendingReq.session_id == SessionORM.id,
                    ViewerPendingReq.requester_client_id == viewer_client_id,
                    ViewerPendingReq.status == "pending",
                ),
            )
            .where(
                SessionORM.gym_id.in_(gym_ids),
                SessionORM.host_client_id != viewer_client_id,
                SessionORM.status == "open",
                _session_not_started_yet(),
                audience_filter,
                rejected_subq,
                already_member_subq,
                host_not_blocked_subq,
                ViewerPendingReq.id.is_(None),
            )
            .order_by(SessionORM.session_date.asc(), SessionORM.session_time.asc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "session_id": r.session_id,
                "host_client_id": r.host_client_id,
                "host_name": r.host_name,
                "host_avatar_url": r.host_avatar,
                "host_bio": r.host_bio,
                "gym_id": r.gym_id,
                "gym_name": r.gym_name,
                "gym_area": (r.gym_area or r.gym_city) or None,
                "session_date": r.session_date,
                "session_time": r.session_time,
                "mate_preference": r.mate_preference,
                "fitness_level": r.fitness_level,
                "workout_vibes": list(r.workout_vibes or []),
                "payment_mode": r.payment_mode,
                "dailypass_booked": r.payment_status == "paid",
                "pending_request_id": r.viewer_pending_request_id,
            }
            for r in rows
        ]

    async def list_session_members(self, session_id: int) -> List[dict]:

        stmt = (
            select(
                SessionMemberORM.client_id,
                SessionMemberORM.role,
                SessionMemberORM.joined_at,
                ClientORM.name,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(SessionMemberORM)
            .join(ClientORM, ClientORM.client_id == SessionMemberORM.client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == SessionMemberORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                SessionMemberORM.session_id == session_id,
                SessionMemberORM.role != "host",
            )
            .order_by(SessionMemberORM.joined_at.asc())
        )
        rows = (await self.db.execute(stmt)).all()
        seen: set = set()
        out: List[dict] = []
        for r in rows:
            if r.client_id in seen:
                continue
            seen.add(r.client_id)
            out.append({
                "client_id": r.client_id,
                "name": r.name,
                "avatar_url": r.avatar,
                "role": r.role,
                "joined_at": r.joined_at,
            })
        return out

    async def count_future_sessions_for_host(self, host_client_id: int) -> int:
        """Open, non-cancelled future sessions hosted by this client."""
        stmt = (
            select(func.count(SessionORM.id))
            .where(
                (SessionORM.host_client_id == host_client_id)
                & (SessionORM.status == "open")
                & _session_not_started_yet()
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    @staticmethod
    def _to_domain(row: SessionRequestORM) -> d.SessionRequest:
        return d.SessionRequest(
            id=row.id,
            session_id=row.session_id,
            requester_client_id=row.requester_client_id,
            host_client_id=row.host_client_id,
            message=d.RequestMessage(row.message) if row.message else None,
            status=d.RequestStatus(row.status),
            created_at=row.created_at,
            responded_at=row.responded_at,
        )
