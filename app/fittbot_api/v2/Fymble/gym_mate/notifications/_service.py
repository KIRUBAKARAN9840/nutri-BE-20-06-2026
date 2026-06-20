"""Orchestrator for the notifications module.

Two responsibilities:
  1. CREATE — callers (event handlers) hand in a Notification value object;
     we persist it, optionally coalesce with a recent one, and enqueue
     the FCM push via Celery.
  2. READ — feed, unread count, mark-read, device-token CRUD.

The service is in-process. It does not import firebase-admin or call FCM
directly — that happens in the Celery worker so the web request returns
without waiting on FCM latency.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._events import (
    EventBus,
    NotificationCreated,
    NoopEventBus,
    PushDispatched,
)
from ._repository import DeviceTokenRepository, NotificationRepository


def _avatar_url_or_none(value: Optional[str]) -> Optional[str]:
    """Same precedence rule the rest of gym_mate uses. Pre-existing
    http(s) URLs (dummy DPs) pass through unchanged."""
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    from app.fittbot_api.v2.Fymble.gym_mate.profile._storage import build_cdn_url
    return build_cdn_url(value)


# Categories that should coalesce within a short window so a burst of
# chat messages from the same sender doesn't spam the user with N
# separate notifications.
COALESCE_WINDOW_SECONDS = 30
COALESCE_CATEGORIES = {
    d.NotificationCategory.CHAT_MESSAGE_DIRECT.value,
    d.NotificationCategory.CHAT_MESSAGE_GROUP.value,
}


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        tokens: DeviceTokenRepository,
        event_bus: Optional[EventBus] = None,
        # Optional injectable for tests; real wiring uses the Celery task.
        dispatch_push=None,
    ):
        self.repo = repository
        self.tokens = tokens
        self.bus = event_bus or NoopEventBus()
        # If not provided, fall back to the real Celery task at call time.
        self._dispatch_push = dispatch_push
        # Optional set the handler wrapper attaches so it can invalidate
        # the home cache for every recipient we wrote a notification to.
        # Stays None when the service is used outside the handler path
        # (e.g. read-only routes), avoiding any extra work.
        self._touched: Optional[set] = None

    # =================================================================
    # WRITE — create notification + enqueue push
    # =================================================================
    async def create(
        self,
        recipient_client_id: int,
        category: d.NotificationCategory,
        title: str,
        body: Optional[str] = None,
        actor_client_id: Optional[int] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
        suppress_push: bool = False,
    ) -> dto.NotificationDTO:
        """The single entry point all event handlers go through.

        `suppress_push=True` — write the row but don't fire FCM. Used
        when the caller knows the recipient is foregrounded on the exact
        surface (e.g. they're inside the chat room that just received a
        message — the WS already delivered).
        """
        # Don't notify yourself — defensive against an event handler
        # that accidentally targets the actor.
        if actor_client_id == recipient_client_id:
            # Still return a stub DTO so callers don't have to branch.
            return dto.NotificationDTO(
                id=0,
                category=category.value,
                title=title,
                body=body,
                actor=None,
                payload=payload or {},
                read_at=None,
                created_at=datetime.now(),
            )

        payload = payload or {}

        # Coalesce burst chats from the same room into a single row.
        coalesced: Optional[dict] = None
        if category.value in COALESCE_CATEGORIES:
            coalesced = await self.repo.get_recent_for_coalesce(
                recipient_client_id=recipient_client_id,
                category=category.value,
                entity_type=entity_type,
                entity_id=entity_id,
                within_seconds=COALESCE_WINDOW_SECONDS,
            )

        if coalesced is not None:
            # Bump count in title — best-effort, no DB count needed.
            prior_count = int(coalesced["payload_json"].get("burst_count", 1))
            new_count = prior_count + 1
            payload = {**payload, "burst_count": new_count}
            new_title = self._coalesce_title(title, new_count)
            await self.repo.update_coalesced(
                notif_id=coalesced["id"],
                title=new_title,
                body=body,
                payload=payload,
            )
            notif_id = coalesced["id"]
            final_title = new_title
            final_body = body
        else:
            notif = d.Notification(
                recipient_client_id=recipient_client_id,
                category=category,
                title=title,
                body=body,
                actor_client_id=actor_client_id,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                created_at=datetime.now(),
            )
            await self.repo.add(notif)
            await self.bus.publish(NotificationCreated(
                notification_id=notif.id,
                recipient_client_id=recipient_client_id,
                category=category.value,
                created_at=notif.created_at,
            ))
            notif_id = notif.id
            final_title = title
            final_body = body

        # Tell the handler wrapper to bust this recipient's home cache
        # after commit, so the three home dots refresh on next /home
        # call instead of waiting out the 60s cache TTL.
        if self._touched is not None:
            self._touched.add(recipient_client_id)

        if not suppress_push:
            await self._enqueue_push(
                notification_id=notif_id,
                recipient_client_id=recipient_client_id,
                title=final_title,
                body=final_body,
                payload=payload,
            )

        # Hydrate a DTO without re-reading the row — the caller usually
        # doesn't render this synchronously.
        return dto.NotificationDTO(
            id=notif_id,
            category=category.value,
            title=final_title,
            body=final_body,
            actor=None,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            read_at=None,
            created_at=datetime.now(),
        )

    @staticmethod
    def _coalesce_title(original_title: str, count: int) -> str:
        """Take 'Aaradhya: hey' → 'Aaradhya: 3 new messages'."""
        sender = original_title.split(":", 1)[0]
        return f"{sender}: {count} new messages"

    async def _enqueue_push(
        self,
        notification_id: int,
        recipient_client_id: int,
        title: str,
        body: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        """Hand off to Celery for FCM send. Lazy-import the task so the
        service module loads cleanly even when Celery isn't configured
        (e.g. in unit tests with a NoopBus and no broker)."""
        if self._dispatch_push is not None:
            await self._dispatch_push(
                notification_id=notification_id,
                recipient_client_id=recipient_client_id,
                title=title,
                body=body,
                data=payload,
            )
            return
        try:
            from app.tasks.gymmate_notification_tasks import (
                dispatch_gymmate_push_task,
            )
            dispatch_gymmate_push_task.delay(
                notification_id=notification_id,
                recipient_client_id=recipient_client_id,
                title=title,
                body=body,
                data=payload,
            )
            await self.bus.publish(PushDispatched(
                notification_id=notification_id,
                recipient_client_id=recipient_client_id,
                token_count=0,  # actual count known only at the worker
            ))
        except Exception:
            # Don't fail the originating HTTP request if Celery is down.
            # The notification row is already in DB and will show in the
            # bell-icon feed on next refresh.
            import logging
            logging.getLogger("gymmate.notifications").exception(
                "failed to enqueue gymmate push (notification %s)",
                notification_id,
            )

    # =================================================================
    # READ — feed
    # =================================================================
    async def list_feed(
        self,
        recipient_client_id: int,
        before_at: Optional[datetime] = None,
        limit: int = 30,
    ) -> dto.NotificationPageDTO:
        capped = max(1, min(limit, 50))
        rows = await self.repo.list_feed(
            recipient_client_id=recipient_client_id,
            before_at=before_at,
            limit=capped,
        )
        has_more = len(rows) > capped
        if has_more:
            rows = rows[:capped]

        items: List[dto.NotificationDTO] = []
        for r in rows:
            actor = None
            if r["actor_client_id"]:
                actor = dto.NotificationActorDTO(
                    client_id=r["actor_client_id"],
                    name=r["actor_name"],
                    avatar_url=_avatar_url_or_none(r["actor_avatar"]),
                )
            items.append(dto.NotificationDTO(
                id=r["id"],
                category=r["category"],
                title=r["title"],
                body=r["body"],
                actor=actor,
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                payload=r["payload_json"] or {},
                read_at=r["read_at"],
                created_at=r["created_at"],
            ))

        next_cursor = items[-1].created_at if (items and has_more) else None
        unread_count = None
        if before_at is None:
            # Include unread badge on the first page so the frontend
            # doesn't need a second roundtrip on inbox open.
            unread_count = await self.repo.unread_count(recipient_client_id)
        return dto.NotificationPageDTO(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            unread_count=unread_count,
        )

    async def unread_count(self, recipient_client_id: int) -> int:
        return await self.repo.unread_count(recipient_client_id)

    async def home_summary(
        self, recipient_client_id: int,
    ) -> dto.HomeNotificationsSummaryDTO:
        """Three home-page dot flags. One SQL round-trip — the repo
        groups by category and we collapse to booleans per the
        tab-routing rule:

          gym_mate_connections.has_received  ← session_request_received
          gym_mate_connections.has_match     ← session_request_accepted /
                                               session_new_match /
                                               session_cancelled_by_host
          friend_requests.has_unread         ← friend_request_*
          chat.has_unread                    ← chat_message_*
        """
        received_cats = {
            d.NotificationCategory.SESSION_REQUEST_RECEIVED.value,
        }
        match_cats = {
            d.NotificationCategory.SESSION_REQUEST_ACCEPTED.value,
            d.NotificationCategory.SESSION_NEW_MATCH.value,
            d.NotificationCategory.SESSION_CANCELLED_BY_HOST.value,
        }
        # Friend-requests home badge = ONLY unseen *received* requests.
        # `friend_request_accepted` (your sent request was accepted) is NOT a
        # request to act on — it stays in the notifications feed and must never
        # drive this unread badge.
        friend_cats = {
            d.NotificationCategory.FRIEND_REQUEST_RECEIVED.value,
        }
        chat_cats = {
            d.NotificationCategory.CHAT_MESSAGE_DIRECT.value,
            d.NotificationCategory.CHAT_MESSAGE_GROUP.value,
        }

        all_categories = sorted(
            received_cats | match_cats | friend_cats | chat_cats,
        )
        raw = await self.repo.unread_summary_by_categories(
            recipient_client_id=recipient_client_id,
            categories=all_categories,
        )

        def _any(cats: set) -> bool:
            return any(
                raw.get(c, {}).get("count", 0) > 0 for c in cats
            )

        def _sum(cats: set) -> int:
            return sum(
                int(raw.get(c, {}).get("count", 0)) for c in cats
            )

        friend_count = _sum(friend_cats)
        return dto.HomeNotificationsSummaryDTO(
            gym_mate_connections=dto.HomeGymMateConnectionsDTO(
                has_received=_any(received_cats),
                has_match=_any(match_cats),
            ),
            friend_requests=dto.HomeFriendRequestsDotDTO(
                has_unread=friend_count > 0,
                count=friend_count,
            ),
            chat=dto.HomeNotificationDotDTO(
                has_unread=_any(chat_cats),
            ),
        )

    async def mark_read(self, recipient_client_id: int, notif_id: int) -> None:
        ok = await self.repo.mark_read(notif_id, recipient_client_id)
        if not ok:
            # Not an error — either already read or not the owner.
            return

    async def mark_all_read(self, recipient_client_id: int) -> int:
        return await self.repo.mark_all_read(recipient_client_id)

    # Allowed groups frontends use to clear a single home dot on tab
    # open. Mapped to category sets so the frontend just sends a
    # symbolic name and we expand.
    #
    # gym_mate_connections splits in two — the user typically opens
    # EITHER the Received tab OR the Matches tab, not both. Send
    # whichever one they opened.
    #
    # "gym_mate_connections" (catch-all) clears both subsets in one
    # call — handy if the frontend ever has a single landing screen
    # that surfaces both.
    BUCKET_TO_CATEGORIES = {
        "gym_mate_received": [
            d.NotificationCategory.SESSION_REQUEST_RECEIVED.value,
        ],
        "gym_mate_match": [
            d.NotificationCategory.SESSION_REQUEST_ACCEPTED.value,
            d.NotificationCategory.SESSION_NEW_MATCH.value,
            d.NotificationCategory.SESSION_CANCELLED_BY_HOST.value,
        ],
        "gym_mate_connections": [
            d.NotificationCategory.SESSION_REQUEST_RECEIVED.value,
            d.NotificationCategory.SESSION_REQUEST_ACCEPTED.value,
            d.NotificationCategory.SESSION_NEW_MATCH.value,
            d.NotificationCategory.SESSION_CANCELLED_BY_HOST.value,
        ],
        # Received-only — opening the Received screen clears unseen incoming
        # requests, and must NOT touch `friend_request_accepted` notifications.
        "friend_requests": [
            d.NotificationCategory.FRIEND_REQUEST_RECEIVED.value,
        ],
        "chat": [
            d.NotificationCategory.CHAT_MESSAGE_DIRECT.value,
            d.NotificationCategory.CHAT_MESSAGE_GROUP.value,
        ],
    }

    async def mark_bucket_read(
        self, recipient_client_id: int, bucket: str,
    ) -> int:
        """Mark every unread notification in this home bucket as read.
        Called by the frontend when the user opens the relevant tab so
        the home-page dot disappears.

        Returns the count of rows updated (was the prior bucket count).
        Unknown bucket names → 400 at the route layer.
        """
        categories = self.BUCKET_TO_CATEGORIES.get(bucket)
        if categories is None:
            from app.utils.logging_utils import FittbotHTTPException
            raise FittbotHTTPException(
                status_code=400,
                detail=(
                    f"Unknown bucket '{bucket}'. Must be one of "
                    f"{list(self.BUCKET_TO_CATEGORIES.keys())}"
                ),
                error_code="GYMMATE_NOTIF_INVALID_BUCKET",
                log_data={
                    "client_id": recipient_client_id, "bucket": bucket,
                },
            )
        return await self.repo.mark_read_by_categories(
            recipient_client_id=recipient_client_id,
            categories=categories,
        )

    async def delete(self, recipient_client_id: int, notif_id: int) -> None:
        await self.repo.remove(notif_id, recipient_client_id)

    # =================================================================
    # WRITE — device tokens
    # =================================================================
    async def register_device_token(
        self, client_id: int, platform: str, token: str,
    ) -> dto.DeviceTokenDTO:
        try:
            normalised = d.validate_platform(platform)
        except d.InvalidPlatform as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_NOTIF_INVALID_PLATFORM",
                log_data={"client_id": client_id, "platform": platform},
            )
        await self.tokens.register(
            client_id=client_id, platform=normalised, token=token,
        )
        return dto.DeviceTokenDTO(token=token, platform=normalised)

    async def unregister_device_token(
        self, client_id: int, token: str,
    ) -> None:
        await self.tokens.unregister(client_id=client_id, token=token)
