"""Event handlers — subscribe to other modules' domain events and turn
them into notification rows.

Each handler:
    1. opens a short-lived async DB session (its own — independent of the
       request that triggered the event, so a notification INSERT failure
       can't roll back the user's primary action)
    2. fetches whatever supplemental data it needs for the title (actor
       name, session details, etc.)
    3. calls `NotificationService.create(...)`
       — which inserts the row AND enqueues the Celery push fan-out
    4. commits its own session

Handlers are wired once at app startup via `register_notification_handlers()`.
"""

import logging
from typing import Awaitable, Callable, List, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_sessionmaker
from app.models.fittbot_models.client import Client as ClientORM
from app.models.fittbot_models.gymmate import (
    GymMateProfile as GymMateProfileORM,
    GymMateProfilePhoto as GymMatePhotoORM,
    GymMateSession as SessionORM,
    GymMateSessionMember as SessionMemberORM,
)
from app.models.fittbot_models.gymmate_chat import (
    GymMateChatRoom as RoomORM,
    GymMateChatParticipant as ParticipantORM,
)

# Event types from each module
from app.fittbot_api.v2.Fymble.gym_mate.friends._events import (
    FriendRequestSent,
    FriendRequestAccepted,
)
from app.fittbot_api.v2.Fymble.gym_mate.sessions._events import (
    SessionCancelled,
    SessionRequestAccepted,
    SessionRequestCreated,
)
from app.fittbot_api.v2.Fymble.gym_mate.chat._events import MessageSent
from app.fittbot_api.v2.Fymble.gym_mate.stories._events import StoryPublished

from . import _domain as d
from ._bus import gymmate_event_bus
from ._repository import DeviceTokenRepository, NotificationRepository
from ._service import NotificationService


logger = logging.getLogger("gymmate.notifications.handlers")


# ── Internal helpers ────────────────────────────────────────────────────────

async def _with_service(fn):
    """Open a short-lived DB session, build the service, run `fn(svc, db)`,
    commit. Failures get logged here — they MUST NOT propagate (caller
    is the event bus which already swallows + logs).

    `_invalidate_home` is collected from any recipient_client_ids the
    `fn` writes to (via the service's `_recipients_touched` hook below)
    so the home-page notification dots show up immediately on next
    refresh instead of waiting out the home-cache 60s TTL.
    """
    Session = get_async_sessionmaker()
    touched: set = set()
    async with Session() as db:
        try:
            svc = NotificationService(
                repository=NotificationRepository(db),
                tokens=DeviceTokenRepository(db),
            )
            # Allow the service to register touched recipients; we bust
            # their home cache AFTER commit.
            svc._touched = touched   # type: ignore[attr-defined]
            await fn(svc, db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("notification handler DB session failed")
            return

    if not touched:
        return
    try:
        from app.utils.redis_config import get_redis
        from app.fittbot_api.v2.Fymble.gym_mate.home._cache import (
            make_home_invalidator,
        )
        redis = await get_redis()
        invalidate = make_home_invalidator(redis)
        for cid in touched:
            await invalidate(cid)
    except Exception:
        # Cache invalidation is best-effort. Worst case is a stale
        # home payload for up to 60s.
        logger.exception("home cache invalidate after notification failed")


async def _client_display_name(db: AsyncSession, client_id: int) -> str:
    """Best-effort name lookup for the actor (e.g. 'Aaradhya'). Falls
    back to 'Someone' so push text is never blank."""
    row = (await db.execute(
        select(ClientORM.name).where(ClientORM.client_id == client_id)
    )).first()
    if row is None or not row.name:
        return "Someone"
    # First word only — title bar is narrow on push.
    return row.name.strip().split(" ", 1)[0]


async def _session_label(db: AsyncSession, session_id: int) -> str:
    """'Bodyfit on May 28' style human-readable label for session-related
    notification bodies."""
    row = (await db.execute(
        select(
            SessionORM.session_date,
            SessionORM.session_time,
        ).where(SessionORM.id == session_id)
    )).first()
    if row is None:
        return "your workout"
    when = row.session_date.strftime("%b %d") if row.session_date else ""
    return f"the workout on {when}" if when else "your workout"


async def _session_members(
    db: AsyncSession, session_id: int,
) -> List[int]:
    rows = (await db.execute(
        select(SessionMemberORM.client_id).where(
            SessionMemberORM.session_id == session_id,
        )
    )).all()
    return [r.client_id for r in rows]


# ── Friends ─────────────────────────────────────────────────────────────────

async def on_friend_request_sent(event: FriendRequestSent) -> None:
    async def inner(svc: NotificationService, db: AsyncSession):
        name = await _client_display_name(db, event.from_client_id)
        await svc.create(
            recipient_client_id=event.to_client_id,
            category=d.NotificationCategory.FRIEND_REQUEST_RECEIVED,
            title=f"{name} sent you a friend request",
            actor_client_id=event.from_client_id,
            entity_type="friend_request",
            entity_id=event.request_id,
            payload={
                "type": d.NotificationCategory.FRIEND_REQUEST_RECEIVED.value,
                "data": d.NotificationTarget.MY_REQUESTS.value,
                "params": {
                    "tab": "received",
                    "request_id": event.request_id,
                    "from_client_id": event.from_client_id,
                },
            },
        )
    await _with_service(inner)


async def on_friend_request_accepted(event: FriendRequestAccepted) -> None:
    async def inner(svc: NotificationService, db: AsyncSession):
        # Recipient of THIS notification = original sender of the request.
        # Actor = the person who just accepted (the request's "to" side).
        name = await _client_display_name(db, event.to_client_id)
        await svc.create(
            recipient_client_id=event.from_client_id,
            category=d.NotificationCategory.FRIEND_REQUEST_ACCEPTED,
            title=f"{name} accepted your friend request",
            actor_client_id=event.to_client_id,
            entity_type="friend_request",
            entity_id=event.request_id,
            payload={
                "type": d.NotificationCategory.FRIEND_REQUEST_ACCEPTED.value,
                "data": d.NotificationTarget.FRIENDS.value,
                "params": {
                    "tab": "my_friends",
                    "peer_client_id": event.to_client_id,
                },
            },
        )
    await _with_service(inner)


# ── Sessions ────────────────────────────────────────────────────────────────

async def on_session_request_created(event: SessionRequestCreated) -> None:
    async def inner(svc: NotificationService, db: AsyncSession):
        name = await _client_display_name(db, event.requester_client_id)
        await svc.create(
            recipient_client_id=event.host_client_id,
            category=d.NotificationCategory.SESSION_REQUEST_RECEIVED,
            title=f"{name} wants to join your workout",
            actor_client_id=event.requester_client_id,
            entity_type="session_request",
            entity_id=event.request_id,
            payload={
                "type": d.NotificationCategory.SESSION_REQUEST_RECEIVED.value,
                "data": d.NotificationTarget.RECEIVED.value,
                "params": {
                    "tab": "received",
                    "session_id": event.session_id,
                    "request_id": event.request_id,
                },
            },
        )
    await _with_service(inner)


async def on_session_request_accepted(event: SessionRequestAccepted) -> None:
    """Two notifications here:
       1. To the requester: "Host accepted your request"
       2. To existing OTHER members of the session: "X joined your workout"
          (the host already knows — they're the one who accepted).
    """
    async def inner(svc: NotificationService, db: AsyncSession):
        # 1) Tell the joiner their request was accepted.
        host_name = await _client_display_name(db, event.host_client_id)
        label = await _session_label(db, event.session_id)
        await svc.create(
            recipient_client_id=event.requester_client_id,
            category=d.NotificationCategory.SESSION_REQUEST_ACCEPTED,
            title=f"{host_name} accepted your join request",
            body=f"You're in for {label}.",
            actor_client_id=event.host_client_id,
            entity_type="session",
            entity_id=event.session_id,
            payload={
                "type": d.NotificationCategory.SESSION_REQUEST_ACCEPTED.value,
                "data": d.NotificationTarget.MATCHES.value,
                "params": {"session_id": event.session_id},
            },
        )

        # 2) Tell existing members (except host + the new joiner) that
        #    someone new just joined.
        members = await _session_members(db, event.session_id)
        new_joiner = event.requester_client_id
        host = event.host_client_id
        joiner_name = await _client_display_name(db, new_joiner)
        for cid in members:
            if cid in (new_joiner, host):
                continue
            await svc.create(
                recipient_client_id=cid,
                category=d.NotificationCategory.SESSION_NEW_MATCH,
                title=f"{joiner_name} joined your workout",
                body=label.capitalize() + ".",
                actor_client_id=new_joiner,
                entity_type="session",
                entity_id=event.session_id,
                payload={
                    "type": d.NotificationCategory.SESSION_NEW_MATCH.value,
                    "data": d.NotificationTarget.MATCHES.value,
                    "params": {
                        "session_id": event.session_id,
                        "new_member_client_id": new_joiner,
                    },
                },
            )
    await _with_service(inner)


async def on_session_cancelled(event: SessionCancelled) -> None:
    """Notify every accepted joiner (not the host — they cancelled it).
    Members already gone (removed via 'leave group') won't be in
    session_member, so they correctly don't get notified."""
    async def inner(svc: NotificationService, db: AsyncSession):
        host_name = await _client_display_name(db, event.host_client_id)
        label = await _session_label(db, event.session_id)
        members = await _session_members(db, event.session_id)
        for cid in members:
            if cid == event.host_client_id:
                continue
            await svc.create(
                recipient_client_id=cid,
                category=d.NotificationCategory.SESSION_CANCELLED_BY_HOST,
                title=f"{host_name} cancelled the workout",
                body=label.capitalize() + ".",
                actor_client_id=event.host_client_id,
                entity_type="session",
                entity_id=event.session_id,
                payload={
                    "type": d.NotificationCategory.SESSION_CANCELLED_BY_HOST.value,
                    "data": d.NotificationTarget.HOME.value,
                    "params": {"session_id": event.session_id},
                },
            )
    await _with_service(inner)


# ── Chat ────────────────────────────────────────────────────────────────────

async def on_message_sent(event: MessageSent) -> None:
    """One notification per non-sender participant. For chatty rooms,
    the service-layer coalesce window collapses bursts into a single
    'N new messages' row."""
    async def inner(svc: NotificationService, db: AsyncSession):
        # Need: room kind (direct vs group), sender name, gym name (groups)
        room_row = (await db.execute(
            select(RoomORM.kind, RoomORM.session_id).where(
                RoomORM.id == event.room_id,
            )
        )).first()
        if room_row is None:
            return

        sender_name = await _client_display_name(db, event.sender_client_id)
        is_group = room_row.kind == "session_group"
        category = (
            d.NotificationCategory.CHAT_MESSAGE_GROUP if is_group
            else d.NotificationCategory.CHAT_MESSAGE_DIRECT
        )

        # Group title is "Session Chat · Sender" so the recipient knows
        # the context without opening. Direct is just the sender name.
        if is_group:
            title = f"Session Chat · {sender_name}"
        else:
            title = sender_name

        # Body could include the message preview, but the message text
        # isn't on the event (intentional — privacy + size). Keep it
        # neutral; the frontend already shows the text in the open chat.
        body = "Sent you a message"

        payload = {
            "type": category.value,
            "data": d.NotificationTarget.CHAT_THREAD.value,
            "params": {
                "room_id": event.room_id,
                "kind": room_row.kind,
                "session_id": room_row.session_id,
                "message_id": event.message_id,
            },
        }

        from app.fittbot_api.v2.Fymble.gym_mate.chat.routes import (
            chat_viewing_key,
        )
        from app.utils.redis_config import get_redis
        redis = await get_redis()
        for cid in event.recipient_ids:
            if cid == event.sender_client_id:
                continue
            # Per-room presence: the recipient is currently INSIDE the
            # same thread iff their viewing key holds this room_id. The
            # FE refreshes that key on every /rooms/{id}/read call while
            # in the thread. Anywhere else (inbox, other thread, app
            # backgrounded) → no key match → push fires normally.
            # Notification row is still created in both cases so the
            # bell-icon history stays correct. Bias-to-delivery on
            # Redis errors.
            in_this_room = False
            try:
                viewing_room_raw = await redis.get(chat_viewing_key(cid))
                if viewing_room_raw is not None:
                    if isinstance(viewing_room_raw, bytes):
                        viewing_room_raw = viewing_room_raw.decode()
                    in_this_room = str(viewing_room_raw) == str(event.room_id)
            except Exception:
                logger.warning("chat viewing GET failed for cid=%s", cid)
            await svc.create(
                recipient_client_id=cid,
                category=category,
                title=title,
                body=body,
                actor_client_id=event.sender_client_id,
                entity_type="room",
                entity_id=event.room_id,
                payload=payload,
                suppress_push=in_this_room,
            )
    await _with_service(inner)


# ── Stories ─────────────────────────────────────────────────────────────────

async def on_story_published(event: StoryPublished) -> None:
    """Notify the author's friends. Per-story for v1 (simple). A future
    daily-digest cron can replace this if it gets noisy."""
    async def inner(svc: NotificationService, db: AsyncSession):
        from app.models.fittbot_models.gymmate import (
            GymMateFriendship as FriendshipORM,
        )
        # Friends of the author
        rows = (await db.execute(
            select(FriendshipORM.client_a_id, FriendshipORM.client_b_id).where(
                (FriendshipORM.client_a_id == event.client_id)
                | (FriendshipORM.client_b_id == event.client_id),
            )
        )).all()
        friend_ids = []
        for a, b in rows:
            other = b if a == event.client_id else a
            friend_ids.append(other)
        if not friend_ids:
            return

        # If audience is 'friends', only friends see it — which is
        # already what we're sending to. If 'public', friends are still
        # the right close-network audience for a push (others see it via
        # discovery / story carousel — no push).
        author_name = await _client_display_name(db, event.client_id)
        for cid in friend_ids:
            await svc.create(
                recipient_client_id=cid,
                category=d.NotificationCategory.STORY_FROM_FRIEND,
                title=f"{author_name} posted a new story",
                actor_client_id=event.client_id,
                entity_type="story",
                entity_id=event.story_id,
                payload={
                    "type": d.NotificationCategory.STORY_FROM_FRIEND.value,
                    "data": d.NotificationTarget.STORIES.value,
                    "params": {
                        "story_id": event.story_id,
                        "author_client_id": event.client_id,
                    },
                },
            )
    await _with_service(inner)


# ── Registration ────────────────────────────────────────────────────────────

_REGISTERED = False


def register_notification_handlers() -> None:
    """Wire all handlers into the shared event bus. Idempotent — calling
    twice has no effect (handlers are stored per-type so a second
    register would double-fire)."""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    gymmate_event_bus.register(FriendRequestSent, on_friend_request_sent)
    gymmate_event_bus.register(FriendRequestAccepted, on_friend_request_accepted)

    gymmate_event_bus.register(SessionRequestCreated, on_session_request_created)
    gymmate_event_bus.register(SessionRequestAccepted, on_session_request_accepted)
    gymmate_event_bus.register(SessionCancelled, on_session_cancelled)

    gymmate_event_bus.register(MessageSent, on_message_sent)
    gymmate_event_bus.register(StoryPublished, on_story_published)

    logger.info("gymmate notification handlers registered")
