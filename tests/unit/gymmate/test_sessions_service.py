from datetime import date, datetime, time, timedelta
from typing import Optional

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.sessions import _domain as d
from app.fittbot_api.v2.Fymble.gym_mate.sessions._events import (
    SessionCancelled,
    SessionCreated,
    SessionPaid,
    SessionRequestAccepted,
    SessionRequestCreated,
    SessionRequestRejected,
    SessionRequestWithdrawn,
)
from app.fittbot_api.v2.Fymble.gym_mate.sessions._service import SessionService
from app.utils.logging_utils import FittbotHTTPException


class InMemorySessionRepository:
    def __init__(self):
        self.rows: dict[int, d.Session] = {}
        self._next = 1

    async def add(self, session):
        session.id = self._next
        self._next += 1
        self.rows[session.id] = session
        return session

    async def get_by_id(self, session_id):
        return self.rows.get(session_id)

    async def update_status(self, session_id, status):
        s = self.rows.get(session_id)
        if s is not None:
            s.status = d.SessionStatus(status)

    async def mark_paid(self, session_id, daily_pass_id):
        s = self.rows.get(session_id)
        if s is not None:
            s.payment_status = d.PaymentStatus.PAID
            s.daily_pass_id = daily_pass_id

    async def set_razorpay_order(self, session_id, order_id):
        s = self.rows.get(session_id)
        if s is not None:
            s.razorpay_order_id = order_id


class InMemorySessionRequestRepository:
    def __init__(self):
        self.requests: dict[int, d.SessionRequest] = {}
        self.members: set[tuple[int, int]] = set()
        self.member_roles: dict[tuple[int, int], str] = {}
        self.client_info: dict[int, tuple[str, str]] = {}
        self._next = 1

    async def add(self, request):
        # Mirror the real repo's ON DUPLICATE KEY UPDATE behavior:
        # if a row already exists for (session_id, requester_client_id),
        # reopen it back to pending instead of duplicating.
        for r in self.requests.values():
            if (r.session_id == request.session_id
                    and r.requester_client_id == request.requester_client_id):
                r.status = d.RequestStatus.PENDING
                r.created_at = datetime.now()
                r.responded_at = None
                r.message = request.message
                request.id = r.id
                request.created_at = r.created_at
                return request
        request.id = self._next
        self._next += 1
        self.requests[request.id] = request
        return request

    async def get_by_id(self, request_id):
        return self.requests.get(request_id)

    async def get_by_session_and_requester(self, session_id, requester_client_id):
        for r in self.requests.values():
            if r.session_id == session_id and r.requester_client_id == requester_client_id:
                return r
        return None

    async def update_status(self, request_id, status, responded_at):
        r = self.requests.get(request_id)
        if r is not None:
            r.status = d.RequestStatus(status)
            r.responded_at = responded_at

    async def add_member(self, session_id, client_id, role="member"):
        key = (session_id, client_id)
        if key in self.members:
            return
        self.members.add(key)
        self.member_roles[key] = role

    async def count_pending_for_host(self, host_client_id):
        return sum(
            1 for r in self.requests.values()
            if r.host_client_id == host_client_id and r.status == d.RequestStatus.PENDING
        )

    async def recent_requester_avatars_for_host(self, host_client_id, limit=3):
        seen = set()
        out = []
        rs = sorted(
            (r for r in self.requests.values()
             if r.host_client_id == host_client_id and r.status == d.RequestStatus.PENDING),
            key=lambda r: r.id, reverse=True,
        )
        for r in rs:
            if r.requester_client_id in seen:
                continue
            seen.add(r.requester_client_id)
            _, avatar = self.client_info.get(r.requester_client_id, (None, None))
            if avatar is None:
                continue
            out.append(avatar)
            if len(out) >= limit:
                break
        return out

    async def list_pending_for_host(self, host_client_id, limit=50, offset=0):
        out = []
        for r in sorted(self.requests.values(), key=lambda r: r.id, reverse=True):
            if r.host_client_id != host_client_id or r.status != d.RequestStatus.PENDING:
                continue
            name, avatar = self.client_info.get(r.requester_client_id, (None, None))
            sess = self.session_repo.rows.get(r.session_id) if self.session_repo else None
            out.append({
                "request_id": r.id,
                "session_id": r.session_id,
                "requester_client_id": r.requester_client_id,
                "requester_name": name,
                "requester_avatar_url": avatar,
                "message": r.message.value if r.message else None,
                "session_date": sess.session_date if sess else None,
                "session_time": sess.session_time if sess else None,
                "gym_id": sess.gym_id if sess else None,
                "gym_name": (self.gym_info.get(sess.gym_id) if sess else None),
                "gym_area": None,
                "created_at": r.created_at,
            })
        return out[offset:offset + limit]

    async def list_pending_for_session(self, session_id, limit=50):
        out = []
        for r in sorted(self.requests.values(), key=lambda r: r.id, reverse=True):
            if r.session_id != session_id or r.status != d.RequestStatus.PENDING:
                continue
            name, avatar = self.client_info.get(r.requester_client_id, (None, None))
            out.append({
                "request_id": r.id,
                "requester_client_id": r.requester_client_id,
                "requester_name": name,
                "requester_avatar_url": avatar,
                "message": r.message.value if r.message else None,
                "created_at": r.created_at,
            })
        return out[:limit]

    async def get_latest_match(self, client_id):
        if self.session_repo is None:
            return None
        # Sessions the viewer is part of (host OR member), newest-first.
        viewer_session_ids = {sid for (sid, cid) in self.members if cid == client_id}
        viewer_sessions = [
            (sid, s) for sid, s in sorted(
                self.session_repo.rows.items(), key=lambda kv: kv[0], reverse=True,
            )
            if sid in viewer_session_ids
            and s.status == d.SessionStatus.OPEN
            and s.session_date >= date.today()
        ]
        # Within each session, walk members newest-first, return the
        # first non-viewer member found.
        members_in_order = list(self.members)
        members_in_order.reverse()
        for sid, sess in viewer_sessions:
            for (m_sid, m_cid) in members_in_order:
                if m_sid != sid or m_cid == client_id:
                    continue
                name, avatar = self.client_info.get(m_cid, (None, None))
                return {
                    "client_id": m_cid,
                    "name": name,
                    "avatar_url": avatar,
                    "session_id": sid,
                    "session_date": sess.session_date,
                }
        return None

    async def count_future_sessions_for_host(self, host_client_id):
        if self.session_repo is None:
            return 0
        return sum(
            1 for s in self.session_repo.rows.values()
            if s.host_client_id == host_client_id
            and s.status == d.SessionStatus.OPEN
            and s.session_date >= date.today()
        )

    async def get_client_basic(self, client_id):
        name, avatar = self.client_info.get(client_id, (None, None))
        return {"client_id": client_id, "name": name, "avatar_url": avatar}

    async def get_client_gender(self, client_id):
        return self.client_gender.get(client_id)

    async def list_sent_requests_for_client(self, client_id, limit=50, offset=0):
        if self.session_repo is None:
            return []
        out = []
        for r in sorted(self.requests.values(), key=lambda r: r.id, reverse=True):
            if r.requester_client_id != client_id or r.status != d.RequestStatus.PENDING:
                continue
            sess = self.session_repo.rows.get(r.session_id)
            if sess is None or sess.status != d.SessionStatus.OPEN or sess.session_date < date.today():
                continue
            hname, havatar = self.client_info.get(sess.host_client_id, (None, None))
            out.append({
                "request_id": r.id,
                "session_id": r.session_id,
                "session_date": sess.session_date,
                "session_time": sess.session_time,
                "host_client_id": sess.host_client_id,
                "host_name": hname,
                "host_avatar_url": havatar,
                "gym_id": sess.gym_id,
                "gym_name": self.gym_info.get(sess.gym_id),
                "gym_area": None,
                "message": r.message.value if r.message else None,
                "created_at": r.created_at,
            })
        return out[offset:offset + limit]

    async def list_hosted_sessions_for_client(self, host_client_id, limit=50):
        if self.session_repo is None:
            return []
        out = []
        for sid, s in self.session_repo.rows.items():
            if (
                s.host_client_id != host_client_id
                or s.status != d.SessionStatus.OPEN
                or s.session_date < date.today()
            ):
                continue
            joiner_count = sum(
                1 for (msid, mcid) in self.members
                if msid == sid and mcid != host_client_id
            )
            out.append({
                "session_id": sid,
                "session_date": s.session_date,
                "session_time": s.session_time,
                "gym_id": s.gym_id,
                "mate_preference": s.mate_preference.value,
                "fitness_level": s.fitness_level.value,
                "workout_vibes": s.workout_vibes.as_list(),
                "payment_mode": s.payment_mode.value,
                "payment_status": s.payment_status.value,
                "status": s.status.value,
                "joiner_count": joiner_count,
                "created_at": None,
            })
        out.sort(key=lambda r: (r["session_date"], r["session_time"]))
        return out[:limit]

    async def list_matched_sessions_for_client(self, client_id, limit=50):
        if self.session_repo is None:
            return []
        viewer_sids = {sid for (sid, cid) in self.members if cid == client_id}
        sessions = {}
        for (sid, cid) in self.members:
            if sid not in viewer_sids:
                continue
            sess = self.session_repo.rows.get(sid)
            if sess is None or sess.status != d.SessionStatus.OPEN or sess.session_date < date.today():
                continue
            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "session_date": sess.session_date,
                    "session_time": sess.session_time,
                    "gym_id": sess.gym_id,
                    "members": [],
                }
            name, avatar = self.client_info.get(cid, (None, None))
            sessions[sid]["members"].append({
                "client_id": cid, "name": name, "avatar_url": avatar,
            })
        result = [s for s in sessions.values() if len(s["members"]) >= 2]
        result.sort(key=lambda r: (r["session_date"], r["session_time"]))
        return result[:limit]

    async def list_all_sessions_at_gyms_with_status(
        self, gym_ids, viewer_client_id, viewer_gender=None, limit=500,
    ):
        rows = await self.list_sessions_at_gyms(
            gym_ids, viewer_client_id, viewer_gender, limit,
        )
        out = []
        for r in rows:
            pending = next(
                (req.id for req in self.requests.values()
                 if req.session_id == r["session_id"]
                 and req.requester_client_id == viewer_client_id
                 and req.status == d.RequestStatus.PENDING),
                None,
            )
            # Hide sessions the viewer already requested to.
            if pending is not None:
                continue
            r["pending_request_id"] = None
            out.append(r)
        return out

    async def list_sessions_at_gyms(
        self, gym_ids, viewer_client_id, viewer_gender=None, limit=200,
    ):
        if not gym_ids or self.session_repo is None:
            return []
        vg = (viewer_gender or "").strip().lower()
        allowed_lower = {"group workout", "unisex", "no preference"}
        if vg in ("male", "female"):
            allowed_lower.add(vg)

        # Mirror the real SQL filter: don't show a session if this
        # viewer was previously REJECTED from it, has a PENDING request
        # to it (lives in /me/sent), or is already an accepted member.
        rejected_sids = {
            r.session_id for r in self.requests.values()
            if r.requester_client_id == viewer_client_id
            and r.status == d.RequestStatus.REJECTED
        }
        pending_sids = {
            r.session_id for r in self.requests.values()
            if r.requester_client_id == viewer_client_id
            and r.status == d.RequestStatus.PENDING
        }
        member_sids = {sid for (sid, cid) in self.members if cid == viewer_client_id}

        out = []
        gym_set = set(gym_ids)
        for sid, s in self.session_repo.rows.items():
            if (
                s.gym_id not in gym_set
                or s.host_client_id == viewer_client_id
                or s.status != d.SessionStatus.OPEN
                or s.session_date < date.today()
                or s.mate_preference.value.lower() not in allowed_lower
                or sid in rejected_sids
                or sid in pending_sids
                or sid in member_sids
            ):
                continue
            host_name, host_avatar = self.client_info.get(
                s.host_client_id, (None, None),
            )
            gym_name = self.gym_info.get(s.gym_id)
            out.append({
                "session_id": sid,
                "host_client_id": s.host_client_id,
                "host_name": host_name,
                "host_avatar_url": host_avatar,
                "host_bio": None,
                "gym_id": s.gym_id,
                "gym_name": gym_name,
                "gym_area": None,
                "session_date": s.session_date,
                "session_time": s.session_time,
                "mate_preference": s.mate_preference.value,
                "fitness_level": s.fitness_level.value,
                "workout_vibes": s.workout_vibes.as_list(),
                "payment_mode": s.payment_mode.value,
                "dailypass_booked": s.payment_status == d.PaymentStatus.PAID,
            })
        out.sort(key=lambda r: (r["session_date"], r["session_time"]))
        return out[:limit]

    gym_info: dict = {}
    client_gender: dict = {}

    session_repo = None  # type: ignore[assignment]


class RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


@pytest.fixture
def repo(): return InMemorySessionRepository()

@pytest.fixture
def req_repo(): return InMemorySessionRequestRepository()

@pytest.fixture
def bus(): return RecordingBus()

@pytest.fixture
def service(repo, req_repo, bus):
    req_repo.session_repo = repo
    return SessionService(repository=repo, request_repository=req_repo, event_bus=bus)


FUTURE_DATE = date.today() + timedelta(days=2)


VALID = dict(
    gym_id=7,
    session_date=FUTURE_DATE,
    session_time=time(10, 30),
    mate_preference="Male",
    fitness_level="Intermediate",
    workout_vibes=["Push Day", "HIIT"],
)


class TestCreatePayLater:
    @pytest.mark.asyncio
    async def test_creates_unpaid_session(self, service, repo):
        result = await service.create_session(
            host_client_id=42, payment_mode="pay_later", **VALID,
        )
        assert result.payment_mode == "pay_later"
        assert result.payment_status == "unpaid"
        assert result.status == "open"
        assert result.daily_pass_id is None
        assert (await repo.get_by_id(result.session_id)) is not None

    @pytest.mark.asyncio
    async def test_publishes_event(self, service, bus):
        await service.create_session(host_client_id=42, payment_mode="pay_later", **VALID)
        assert len(bus.events) == 1
        assert isinstance(bus.events[0], SessionCreated)
        assert bus.events[0].payment_status == "unpaid"

    @pytest.mark.asyncio
    async def test_default_payment_mode_is_pay_later(self, service):
        result = await service.create_session(host_client_id=42, **VALID)
        assert result.payment_mode == "pay_later"

    @pytest.mark.asyncio
    async def test_invalid_mate_preference_rejected(self, service):
        bad = {**VALID, "mate_preference": "Robot"}
        with pytest.raises(FittbotHTTPException) as exc:
            await service.create_session(host_client_id=42, payment_mode="pay_later", **bad)
        assert exc.value.error_code == "GYMMATE_SESSION_INVALID"

    @pytest.mark.asyncio
    async def test_invalid_fitness_level_rejected(self, service):
        bad = {**VALID, "fitness_level": "Pro"}
        with pytest.raises(FittbotHTTPException):
            await service.create_session(host_client_id=42, payment_mode="pay_later", **bad)

    @pytest.mark.asyncio
    async def test_unknown_vibe_rejected(self, service):
        bad = {**VALID, "workout_vibes": ["Underwater Yoga"]}
        with pytest.raises(FittbotHTTPException):
            await service.create_session(host_client_id=42, payment_mode="pay_later", **bad)

    @pytest.mark.asyncio
    async def test_empty_vibes_rejected(self, service):
        bad = {**VALID, "workout_vibes": []}
        with pytest.raises(FittbotHTTPException):
            await service.create_session(host_client_id=42, payment_mode="pay_later", **bad)


class TestCreatePayNow:
    @pytest.mark.asyncio
    async def test_creates_pending_session(self, service):
        result = await service.create_session(
            host_client_id=42, payment_mode="pay_now", **VALID,
        )
        assert result.payment_mode == "pay_now"
        assert result.payment_status == "pending"


class TestGetSession:
    @pytest.mark.asyncio
    async def test_returns_session(self, service):
        created = await service.create_session(host_client_id=42, payment_mode="pay_later", **VALID)
        fetched = await service.get_session(requester_client_id=42, session_id=created.session_id)
        assert fetched.session_id == created.session_id
        assert fetched.gym_id == 7

    @pytest.mark.asyncio
    async def test_not_found(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.get_session(requester_client_id=42, session_id=9999)
        assert exc.value.status_code == 404


class TestCancelSession:
    @pytest.mark.asyncio
    async def test_host_can_cancel(self, service, repo, bus):
        created = await service.create_session(host_client_id=42, payment_mode="pay_later", **VALID)
        await service.cancel_session(requester_client_id=42, session_id=created.session_id)
        stored = await repo.get_by_id(created.session_id)
        assert stored.status is d.SessionStatus.CANCELLED
        assert any(isinstance(e, SessionCancelled) for e in bus.events)

    @pytest.mark.asyncio
    async def test_non_host_forbidden(self, service):
        created = await service.create_session(host_client_id=42, payment_mode="pay_later", **VALID)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.cancel_session(requester_client_id=99, session_id=created.session_id)
        assert exc.value.status_code == 403
        assert exc.value.error_code == "GYMMATE_SESSION_FORBIDDEN"

    @pytest.mark.asyncio
    async def test_double_cancel_rejected(self, service):
        created = await service.create_session(host_client_id=42, payment_mode="pay_later", **VALID)
        await service.cancel_session(requester_client_id=42, session_id=created.session_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.cancel_session(requester_client_id=42, session_id=created.session_id)
        assert exc.value.error_code == "GYMMATE_SESSION_BAD_STATE"

    @pytest.mark.asyncio
    async def test_not_found(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.cancel_session(requester_client_id=42, session_id=9999)
        assert exc.value.status_code == 404


class TestMarkPaidWebhook:
    @pytest.mark.asyncio
    async def test_flips_to_paid(self, service, repo, bus):
        created = await service.create_session(host_client_id=42, payment_mode="pay_now", **VALID)
        bus.events.clear()

        await service.mark_paid_via_webhook(
            session_id=created.session_id, daily_pass_id="dps_xyz",
        )
        stored = await repo.get_by_id(created.session_id)
        assert stored.payment_status is d.PaymentStatus.PAID
        assert stored.daily_pass_id == "dps_xyz"
        assert any(isinstance(e, SessionPaid) for e in bus.events)

    @pytest.mark.asyncio
    async def test_idempotent_on_repeat(self, service, bus):
        created = await service.create_session(host_client_id=42, payment_mode="pay_now", **VALID)
        await service.mark_paid_via_webhook(created.session_id, "dps_xyz")
        bus.events.clear()
        await service.mark_paid_via_webhook(created.session_id, "dps_other")
        # second call is a no-op (already paid)
        assert bus.events == []

    @pytest.mark.asyncio
    async def test_silently_ignores_missing_session(self, service):
        await service.mark_paid_via_webhook(session_id=9999, daily_pass_id="dps_xyz")


class TestSendRequest:
    @pytest.mark.asyncio
    async def test_send_creates_pending(self, service, req_repo, bus):
        created = await service.create_session(host_client_id=42, **VALID)
        bus.events.clear()

        result = await service.send_request(
            requester_client_id=99, session_id=created.session_id,
            message="leg day twin?",
        )
        assert result.status == "pending"
        assert result.requester_client_id == 99
        assert result.host_client_id == 42
        assert result.message == "leg day twin?"
        assert any(isinstance(e, SessionRequestCreated) for e in bus.events)

    @pytest.mark.asyncio
    async def test_self_request_rejected(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.send_request(
                requester_client_id=42, session_id=created.session_id,
            )
        assert exc.value.error_code == "GYMMATE_REQUEST_INVALID"

    @pytest.mark.asyncio
    async def test_request_session_not_found(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.send_request(requester_client_id=99, session_id=9999)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_request_to_cancelled_session_rejected(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        await service.cancel_session(requester_client_id=42, session_id=created.session_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.send_request(
                requester_client_id=99, session_id=created.session_id,
            )
        assert exc.value.error_code == "GYMMATE_REQUEST_INVALID"

    @pytest.mark.asyncio
    async def test_duplicate_pending_is_idempotent(self, service, bus):
        created = await service.create_session(host_client_id=42, **VALID)
        first = await service.send_request(requester_client_id=99, session_id=created.session_id)
        bus.events.clear()
        second = await service.send_request(requester_client_id=99, session_id=created.session_id)
        assert first.request_id == second.request_id
        assert bus.events == []

    @pytest.mark.asyncio
    async def test_already_accepted_blocks_new_request(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.accept_request(host_client_id=42, request_id=r.request_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.send_request(
                requester_client_id=99, session_id=created.session_id,
            )
        assert exc.value.error_code == "GYMMATE_REQUEST_ALREADY_ACCEPTED"

    @pytest.mark.asyncio
    async def test_rejection_is_sticky_cant_resend(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.reject_request(host_client_id=42, request_id=r.request_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.send_request(
                requester_client_id=99, session_id=created.session_id,
            )
        assert exc.value.error_code == "GYMMATE_REQUEST_PREVIOUSLY_REJECTED"

    @pytest.mark.asyncio
    async def test_withdrawn_can_be_resent(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.withdraw_request(requester_client_id=99, request_id=r.request_id)
        # Re-send is allowed since the requester withdrew of their own accord
        again = await service.send_request(
            requester_client_id=99, session_id=created.session_id,
        )
        assert again.status == "pending"

    @pytest.mark.asyncio
    async def test_message_too_long_rejected(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        with pytest.raises(FittbotHTTPException):
            await service.send_request(
                requester_client_id=99, session_id=created.session_id,
                message="x" * 281,
            )


class TestAcceptReject:
    @pytest.mark.asyncio
    async def test_host_can_accept(self, service, req_repo, bus):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        bus.events.clear()

        await service.accept_request(host_client_id=42, request_id=r.request_id)
        stored = await req_repo.get_by_id(r.request_id)
        assert stored.status is d.RequestStatus.ACCEPTED
        assert (created.session_id, 99) in req_repo.members
        assert any(isinstance(e, SessionRequestAccepted) for e in bus.events)

    @pytest.mark.asyncio
    async def test_non_host_cannot_accept(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.accept_request(host_client_id=88, request_id=r.request_id)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_double_accept_rejected(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.accept_request(host_client_id=42, request_id=r.request_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.accept_request(host_client_id=42, request_id=r.request_id)
        assert exc.value.error_code == "GYMMATE_REQUEST_BAD_STATE"

    @pytest.mark.asyncio
    async def test_host_can_reject(self, service, req_repo, bus):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        bus.events.clear()

        await service.reject_request(host_client_id=42, request_id=r.request_id)
        stored = await req_repo.get_by_id(r.request_id)
        assert stored.status is d.RequestStatus.REJECTED
        assert (created.session_id, 99) not in req_repo.members
        assert any(isinstance(e, SessionRequestRejected) for e in bus.events)

    @pytest.mark.asyncio
    async def test_request_not_found(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.accept_request(host_client_id=42, request_id=9999)
        assert exc.value.status_code == 404


class TestWithdraw:
    @pytest.mark.asyncio
    async def test_requester_can_withdraw(self, service, req_repo, bus):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        bus.events.clear()

        await service.withdraw_request(requester_client_id=99, request_id=r.request_id)
        stored = await req_repo.get_by_id(r.request_id)
        assert stored.status is d.RequestStatus.WITHDRAWN
        assert any(isinstance(e, SessionRequestWithdrawn) for e in bus.events)

    @pytest.mark.asyncio
    async def test_other_user_cannot_withdraw(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.withdraw_request(requester_client_id=88, request_id=r.request_id)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_withdraw_accepted(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.accept_request(host_client_id=42, request_id=r.request_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.withdraw_request(requester_client_id=99, request_id=r.request_id)
        assert exc.value.error_code == "GYMMATE_REQUEST_BAD_STATE"


class TestListing:
    @pytest.mark.asyncio
    async def test_list_pending_for_session_host_only(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.send_request(requester_client_id=88, session_id=created.session_id)

        rows = await service.list_pending_for_session(
            requester_client_id=42, session_id=created.session_id,
        )
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_list_pending_non_host_forbidden(self, service):
        created = await service.create_session(host_client_id=42, **VALID)
        await service.send_request(requester_client_id=99, session_id=created.session_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await service.list_pending_for_session(
                requester_client_id=88, session_id=created.session_id,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_inbox_only_pending_for_my_sessions(self, service):
        mine = await service.create_session(host_client_id=42, **VALID)
        theirs = await service.create_session(host_client_id=88, **VALID)
        await service.send_request(requester_client_id=99, session_id=mine.session_id)
        await service.send_request(requester_client_id=99, session_id=theirs.session_id)

        rows = await service.list_inbox(host_client_id=42)
        assert len(rows) == 1
        assert rows[0].session_id == mine.session_id


class TestHostSummary:
    @pytest.mark.asyncio
    async def test_all_null_when_no_sessions(self, service):
        summary = await service.get_host_summary(host_client_id=42)
        assert summary.future_count is None
        assert summary.received_requests is None
        assert summary.match is None
        # Host identity is still present (always available from JWT)
        assert summary.host.client_id == 42

    @pytest.mark.asyncio
    async def test_received_requests_null_when_no_pending(self, service):
        await service.create_session(host_client_id=42, **VALID)
        summary = await service.get_host_summary(host_client_id=42)
        assert summary.future_count == 1
        assert summary.received_requests is None
        assert summary.match is None

    @pytest.mark.asyncio
    async def test_counts_future_sessions(self, service):
        await service.create_session(host_client_id=42, **VALID)
        await service.create_session(host_client_id=42, **VALID)
        summary = await service.get_host_summary(host_client_id=42)
        assert summary.future_count == 2

    @pytest.mark.asyncio
    async def test_pending_count_and_avatars(self, service, req_repo):
        created = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[99] = ("Anamika", "https://x/a.jpg")
        req_repo.client_info[88] = ("Bharath", "https://x/b.jpg")
        await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.send_request(requester_client_id=88, session_id=created.session_id)

        summary = await service.get_host_summary(host_client_id=42)
        assert summary.received_requests.pending_count == 2
        urls = summary.received_requests.recent_avatars
        assert set(urls) == {"https://x/a.jpg", "https://x/b.jpg"}

    @pytest.mark.asyncio
    async def test_avatars_capped_at_three(self, service, req_repo):
        created = await service.create_session(host_client_id=42, **VALID)
        for cid in (99, 88, 77, 66, 55):
            req_repo.client_info[cid] = (f"u{cid}", f"https://x/{cid}.jpg")
            await service.send_request(requester_client_id=cid, session_id=created.session_id)
        summary = await service.get_host_summary(host_client_id=42)
        assert len(summary.received_requests.recent_avatars) == 3

    @pytest.mark.asyncio
    async def test_match_after_accept(self, service, req_repo):
        created = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[99] = ("Anamika", "https://x/a.jpg")
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.accept_request(host_client_id=42, request_id=r.request_id)

        summary = await service.get_host_summary(host_client_id=42)
        assert summary.match is not None
        assert summary.match.client_id == 99
        assert summary.match.name == "Anamika"
        # Host themselves should never be the match
        assert summary.match.client_id != 42

    @pytest.mark.asyncio
    async def test_rejected_request_not_a_match(self, service, req_repo):
        created = await service.create_session(host_client_id=42, **VALID)
        r = await service.send_request(requester_client_id=99, session_id=created.session_id)
        await service.reject_request(host_client_id=42, request_id=r.request_id)

        summary = await service.get_host_summary(host_client_id=42)
        assert summary.match is None


class TestListNearbyGymMates:
    @pytest.mark.asyncio
    async def test_empty_distance_map_returns_empty(self, service):
        result = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_sorts_by_distance_then_date(self, service, repo, req_repo):
        # Sessions with mate_preference="Male" — viewer 99 is Male so they match
        await service.create_session(host_client_id=42, **{**VALID, "gym_id": 11})
        await service.create_session(host_client_id=42, **{**VALID, "gym_id": 22})
        req_repo.client_info[42] = ("Raj", "https://x/r.jpg")
        req_repo.gym_info = {11: "FarGym", 22: "NearGym"}
        req_repo.client_gender = {99: "Male"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99,
            distance_map={22: 1.2, 11: 9.8},
        )
        assert len(result) == 2
        assert result[0].gym_id == 22
        assert result[0].distance_km == 1.2
        assert result[0].host_name == "Raj"
        assert result[1].gym_id == 11
        assert result[1].distance_km == 9.8
        # sno is 1-indexed in final sorted order
        assert [r.sno for r in result] == [1, 2]

    @pytest.mark.asyncio
    async def test_excludes_viewers_own_sessions(self, service, req_repo):
        await service.create_session(host_client_id=99, **{**VALID, "gym_id": 11})
        req_repo.client_info[99] = ("Self", None)
        req_repo.client_gender = {99: "Male"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99,
            distance_map={11: 0.5},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_respects_limit(self, service, req_repo):
        for gid in (11, 22, 33):
            await service.create_session(host_client_id=42, **{**VALID, "gym_id": gid})
        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G1", 22: "G2", 33: "G3"}
        req_repo.client_gender = {99: "Male"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99,
            distance_map={11: 1.0, 22: 2.0, 33: 3.0},
            limit=2,
        )
        assert len(result) == 2
        assert [r.gym_id for r in result] == [11, 22]

    @pytest.mark.asyncio
    async def test_viewer_gender_is_normalized_case_insensitive(
        self, service, req_repo,
    ):
        """clients.gender is freeform ('male'), enum is title-case ('Male').
        Normalize before SQL so a real-user case-mismatch doesn't hide
        the entire feed."""
        await service.create_session(
            host_client_id=42, **{**VALID, "gym_id": 11, "mate_preference": "Male"},
        )
        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G"}
        # Lowercase gender — common in user-entered data
        req_repo.client_gender = {99: "male"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={11: 1.0},
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_male_only_session_hidden_from_female_viewer(
        self, service, req_repo,
    ):
        await service.create_session(
            host_client_id=42, **{**VALID, "gym_id": 11, "mate_preference": "Male"},
        )
        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G"}
        req_repo.client_gender = {99: "Female"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={11: 1.0},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_female_only_session_hidden_from_male_viewer(
        self, service, req_repo,
    ):
        await service.create_session(
            host_client_id=42, **{**VALID, "gym_id": 11, "mate_preference": "Female"},
        )
        req_repo.client_info[42] = ("Anamika", None)
        req_repo.gym_info = {11: "G"}
        req_repo.client_gender = {99: "Male"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={11: 1.0},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_group_workout_visible_to_anyone(self, service, req_repo):
        await service.create_session(
            host_client_id=42,
            **{**VALID, "gym_id": 11, "mate_preference": "Group Workout"},
        )
        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G"}
        # Viewer has no gender set → still sees the group session
        req_repo.client_gender = {}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={11: 1.0},
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_preference_visible_to_anyone(self, service, req_repo):
        await service.create_session(
            host_client_id=42,
            **{**VALID, "gym_id": 11, "mate_preference": "No Preference"},
        )
        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G"}
        req_repo.client_gender = {99: "Other"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={11: 1.0},
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_dailypass_booked_reflects_payment_status(self, service, repo, req_repo):
        paid = await service.create_session(host_client_id=42, **{**VALID, "gym_id": 11})
        unpaid = await service.create_session(host_client_id=42, **{**VALID, "gym_id": 22})
        # Mark one session as paid (simulating dailypass webhook)
        await service.mark_paid_via_webhook(paid.session_id, daily_pass_id="dps_xyz")

        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G1", 22: "G2"}
        req_repo.client_gender = {99: "Male"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99,
            distance_map={11: 1.0, 22: 2.0},
        )
        by_gym = {r.gym_id: r for r in result}
        assert by_gym[11].dailypass_booked is True
        assert by_gym[22].dailypass_booked is False

    @pytest.mark.asyncio
    async def test_rejected_session_hidden_from_nearby(self, service, req_repo):
        """A session disappears from the viewer's nearby_gym_mates once
        the host has rejected their request — rejection is sticky."""
        host_session = await service.create_session(host_client_id=42, **{**VALID, "gym_id": 11})
        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G"}
        req_repo.client_gender = {99: "Male"}

        # Before rejection: 99 sees the session in nearby
        before = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={11: 1.0},
        )
        assert len(before) == 1

        # 99 sends a request → host rejects
        r = await service.send_request(requester_client_id=99, session_id=host_session.session_id)
        await service.reject_request(host_client_id=42, request_id=r.request_id)

        # After rejection: 99 no longer sees that session
        after = await service.list_nearby_gym_mates(
            viewer_client_id=99, distance_map={11: 1.0},
        )
        assert after == []

    @pytest.mark.asyncio
    async def test_view_all_hides_sessions_already_requested(self, service, req_repo):
        """View-all filters out sessions the viewer has already sent a
        request to — they belong in /me/sent, not in discovery."""
        s1 = await service.create_session(host_client_id=42, **{**VALID, "gym_id": 11})
        s2 = await service.create_session(host_client_id=88, **{**VALID, "gym_id": 22})
        req_repo.client_info[42] = ("Raj", None)
        req_repo.client_info[88] = ("Bharath", None)
        req_repo.gym_info = {11: "G1", 22: "G2"}
        req_repo.client_gender = {99: "Male"}

        # Viewer 99 has already sent a request to s1, none to s2
        await service.send_request(requester_client_id=99, session_id=s1.session_id)

        result = await service.list_all_nearby_gym_mates(
            viewer_client_id=99,
            distance_map={11: 1.0, 22: 2.0},
        )
        # s1 hidden, only s2 surfaces
        assert len(result) == 1
        assert result[0].session_id == s2.session_id
        assert result[0].request_status == "none"
        assert result[0].pending_request_id is None

    @pytest.mark.asyncio
    async def test_other_gender_only_sees_open_to_all(self, service, req_repo):
        # Three sessions: Male / Female / Group Workout — viewer is "Other"
        await service.create_session(
            host_client_id=42,
            **{**VALID, "gym_id": 11, "mate_preference": "Male"},
        )
        await service.create_session(
            host_client_id=42,
            **{**VALID, "gym_id": 22, "mate_preference": "Female"},
        )
        await service.create_session(
            host_client_id=42,
            **{**VALID, "gym_id": 33, "mate_preference": "Group Workout"},
        )
        req_repo.client_info[42] = ("Raj", None)
        req_repo.gym_info = {11: "G1", 22: "G2", 33: "G3"}
        req_repo.client_gender = {99: "Other"}

        result = await service.list_nearby_gym_mates(
            viewer_client_id=99,
            distance_map={11: 1.0, 22: 2.0, 33: 3.0},
        )
        # Only the Group Workout one survives
        assert len(result) == 1
        assert result[0].gym_id == 33

    @pytest.mark.asyncio
    async def test_match_shows_host_when_viewer_is_a_joiner(self, service, req_repo):
        # Viewer (99) has hosted nothing — they joined someone else's session.
        hosts_session = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[42] = ("Raj-the-host", "https://x/r.jpg")

        r = await service.send_request(requester_client_id=99, session_id=hosts_session.session_id)
        await service.accept_request(host_client_id=42, request_id=r.request_id)

        summary = await service.get_host_summary(host_client_id=99)
        # Viewer has zero hosted future sessions, but is matched with the host
        assert summary.future_count is None
        assert summary.received_requests is None
        assert summary.match is not None
        assert summary.match.client_id == 42
        assert summary.match.name == "Raj-the-host"
        assert summary.match.session_id == hosts_session.session_id

    @pytest.mark.asyncio
    async def test_match_is_latest_accepted_in_recent_session(self, service, req_repo):
        # Older session with one match (Anamika)
        older = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[99] = ("Anamika", "https://x/a.jpg")
        req_repo.client_info[88] = ("Bharath", "https://x/b.jpg")
        r1 = await service.send_request(requester_client_id=99, session_id=older.session_id)
        await service.accept_request(host_client_id=42, request_id=r1.request_id)

        # Newer session with a different match (Bharath)
        newer = await service.create_session(host_client_id=42, **VALID)
        r2 = await service.send_request(requester_client_id=88, session_id=newer.session_id)
        await service.accept_request(host_client_id=42, request_id=r2.request_id)

        summary = await service.get_host_summary(host_client_id=42)
        # Most recent session wins, then latest accept inside it
        assert summary.match is not None
        assert summary.match.client_id == 88
        assert summary.match.session_id == newer.session_id


class TestListSent:
    @pytest.mark.asyncio
    async def test_lists_my_pending_sent_requests(self, service, req_repo):
        host_session = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[42] = ("Raj", "https://x/r.jpg")
        await service.send_request(requester_client_id=99, session_id=host_session.session_id)

        rows = await service.list_sent(client_id=99)
        assert len(rows) == 1
        assert rows[0].host_client_id == 42
        assert rows[0].host_name == "Raj"
        assert rows[0].session_id == host_session.session_id

    @pytest.mark.asyncio
    async def test_excludes_after_accept(self, service, req_repo):
        host_session = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[42] = ("Raj", None)
        r = await service.send_request(requester_client_id=99, session_id=host_session.session_id)
        await service.accept_request(host_client_id=42, request_id=r.request_id)

        # Not pending anymore → not in "sent"
        rows = await service.list_sent(client_id=99)
        assert rows == []


class TestListHostedSessions:
    @pytest.mark.asyncio
    async def test_returns_my_open_future_sessions_with_zero_joiners(
        self, service, req_repo,
    ):
        s1 = await service.create_session(host_client_id=42, **VALID)
        # Sole member is the host — nobody has joined yet
        rows = await service.list_hosted_sessions(host_client_id=42)
        assert len(rows) == 1
        assert rows[0].session_id == s1.session_id
        assert rows[0].joiner_count == 0
        # Create-time details surfaced verbatim
        assert rows[0].mate_preference == VALID["mate_preference"]
        assert rows[0].fitness_level == VALID["fitness_level"]
        assert rows[0].workout_vibes == VALID["workout_vibes"]
        assert rows[0].status == "open"

    @pytest.mark.asyncio
    async def test_joiner_count_grows_on_accept(self, service, req_repo):
        s1 = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[99] = ("Anamika", None)
        r = await service.send_request(requester_client_id=99, session_id=s1.session_id)
        await service.accept_request(host_client_id=42, request_id=r.request_id)

        rows = await service.list_hosted_sessions(host_client_id=42)
        assert rows[0].joiner_count == 1

    @pytest.mark.asyncio
    async def test_multiple_sessions_sorted_by_nearest(self, service, req_repo):
        from datetime import timedelta
        far = await service.create_session(
            host_client_id=42,
            **{**VALID, "session_date": date.today() + timedelta(days=5)},
        )
        near = await service.create_session(
            host_client_id=42,
            **{**VALID, "session_date": date.today() + timedelta(days=1)},
        )
        rows = await service.list_hosted_sessions(host_client_id=42)
        assert [r.session_id for r in rows] == [near.session_id, far.session_id]

    @pytest.mark.asyncio
    async def test_cancelled_session_hidden(self, service):
        s1 = await service.create_session(host_client_id=42, **VALID)
        await service.cancel_session(requester_client_id=42, session_id=s1.session_id)
        rows = await service.list_hosted_sessions(host_client_id=42)
        assert rows == []


class TestListMyMatches:
    @pytest.mark.asyncio
    async def test_lists_matches_as_host_and_joiner(self, service, req_repo):
        # 42 hosts a session; 99 joins
        s1 = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[42] = ("Raj", "https://x/r.jpg")
        req_repo.client_info[99] = ("Anamika", "https://x/a.jpg")
        r1 = await service.send_request(requester_client_id=99, session_id=s1.session_id)
        await service.accept_request(host_client_id=42, request_id=r1.request_id)

        # Each viewer sees the session entry with BOTH members
        for viewer, expected_other in [(42, 99), (99, 42)]:
            matches = await service.list_my_matches(client_id=viewer)
            assert len(matches) == 1
            sess = matches[0]
            assert sess.session_id == s1.session_id
            member_ids = {m.client_id for m in sess.members}
            assert member_ids == {42, 99}
            # The viewer is marked
            viewer_entries = [m for m in sess.members if m.is_viewer]
            assert len(viewer_entries) == 1
            assert viewer_entries[0].client_id == viewer

    @pytest.mark.asyncio
    async def test_multiple_members_in_same_session(self, service, req_repo):
        s1 = await service.create_session(host_client_id=42, **VALID)
        req_repo.client_info[42] = ("Raj", None)
        req_repo.client_info[99] = ("Anamika", None)
        req_repo.client_info[88] = ("Bharath", None)
        r1 = await service.send_request(requester_client_id=99, session_id=s1.session_id)
        await service.accept_request(host_client_id=42, request_id=r1.request_id)
        r2 = await service.send_request(requester_client_id=88, session_id=s1.session_id)
        await service.accept_request(host_client_id=42, request_id=r2.request_id)

        matches = await service.list_my_matches(client_id=42)
        assert len(matches) == 1
        member_ids = {m.client_id for m in matches[0].members}
        assert member_ids == {42, 99, 88}

    @pytest.mark.asyncio
    async def test_sorted_by_nearest_session_first(self, service, req_repo):
        from datetime import timedelta
        s_far = await service.create_session(
            host_client_id=42,
            **{**VALID, "session_date": date.today() + timedelta(days=5)},
        )
        s_near = await service.create_session(
            host_client_id=42,
            **{**VALID, "session_date": date.today() + timedelta(days=1)},
        )
        req_repo.client_info[42] = ("Raj", None)
        req_repo.client_info[99] = ("A", None)
        for sid in (s_far.session_id, s_near.session_id):
            r = await service.send_request(requester_client_id=99, session_id=sid)
            await service.accept_request(host_client_id=42, request_id=r.request_id)

        matches = await service.list_my_matches(client_id=42)
        # Near one first
        assert matches[0].session_id == s_near.session_id
        assert matches[1].session_id == s_far.session_id
