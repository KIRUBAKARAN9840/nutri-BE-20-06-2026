"""HTTP routes — the notification center feed + device-token CRUD.

Push delivery itself happens out of band via Celery (see
`app.tasks.gymmate_notification_tasks`), so these endpoints stay
read-mostly and fast."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .api import NotificationsAPI, build_notifications_api
from ._http_schemas import (
    DeviceTokenResponse,
    EmptyResponse,
    MarkBucketReadRequest,
    NotificationFeedResponse,
    RegisterDeviceTokenRequest,
    UnreadCountResponse,
)
from .schemas import UnreadCountDTO


router = APIRouter(
    prefix="/gym_mate/notifications",
    tags=["GymMate Notifications V2"],
)


def _api(db: AsyncSession = Depends(get_async_db)) -> NotificationsAPI:
    return build_notifications_api(db)


# ── Feed ────────────────────────────────────────────────────────────────────

@router.get("", response_model=NotificationFeedResponse)
@log_exceptions
async def list_notifications(
    request: Request,
    before_at: Optional[datetime] = Query(
        None,
        description="Cursor: pass `next_cursor` from the previous page.",
    ),
    limit: int = Query(30, ge=1, le=50),
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    """Paginated bell-icon feed. `unread_count` is on the first page
    only (when no cursor) so the badge paints without a second call."""
    page = await api.list_feed(
        recipient_client_id=client_id,
        before_at=before_at,
        limit=limit,
    )
    return NotificationFeedResponse(data=page)


@router.get("/unread-count", response_model=UnreadCountResponse)
@log_exceptions
async def get_unread_count(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    """Light endpoint for the red-dot badge — used by the bell-icon
    poll when the inbox payload isn't already loaded."""
    count = await api.unread_count(recipient_client_id=client_id)
    return UnreadCountResponse(data=UnreadCountDTO(count=count))


@router.post("/mark-all-read", response_model=UnreadCountResponse)
@log_exceptions
async def mark_all_read(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    """User tapped the bell — clear the badge in one call."""
    await api.mark_all_read(recipient_client_id=client_id)
    await db.commit()
    return UnreadCountResponse(data=UnreadCountDTO(count=0))


@router.post("/mark-bucket-read", response_model=EmptyResponse)
@log_exceptions
async def mark_bucket_read(
    body: MarkBucketReadRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    """Clear one home-page dot. Frontend calls this when the user
    opens the tab that bucket points at:

      bucket="gym_mate_connections"  → opens My Requests / Matches
      bucket="friend_requests"       → opens Friends → Received
      bucket="chat"                  → opens Chat tab

    Marks every unread notification in that bucket's category set as
    read. The next /home call will see `unread_count: 0` for the
    bucket and the frontend stops painting the dot."""
    await api.mark_bucket_read(
        recipient_client_id=client_id, bucket=body.bucket,
    )
    await db.commit()
    # Bust the home cache so the dot disappears on next /home — without
    # this the user would see a stale dot for up to 60s (the home TTL).
    try:
        from app.utils.redis_config import get_redis
        from app.fittbot_api.v2.Fymble.gym_mate.home._cache import (
            make_home_invalidator,
        )
        redis = await get_redis()
        await make_home_invalidator(redis)(client_id)
    except Exception:
        # Cache invalidation is best-effort — worst case the dot lingers
        # for ≤60s. The DB row is already updated.
        pass
    return EmptyResponse()


@router.post("/{notification_id}/read", response_model=EmptyResponse)
@log_exceptions
async def mark_notification_read(
    notification_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    await api.mark_read(
        recipient_client_id=client_id, notif_id=notification_id,
    )
    await db.commit()
    return EmptyResponse()


@router.delete("/{notification_id}", response_model=EmptyResponse)
@log_exceptions
async def delete_notification(
    notification_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    await api.delete(
        recipient_client_id=client_id, notif_id=notification_id,
    )
    await db.commit()
    return EmptyResponse()


# ── Device tokens (FCM) ────────────────────────────────────────────────────

@router.post("/device-token", response_model=DeviceTokenResponse)
@log_exceptions
async def register_device_token(
    body: RegisterDeviceTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    """Frontend calls this after FCM hands it a token (first launch +
    on token rotation). Idempotent — re-registering the same token is
    a no-op."""
    data = await api.register_device_token(
        client_id=client_id, platform=body.platform, token=body.token,
    )
    await db.commit()
    return DeviceTokenResponse(data=data)


@router.delete("/device-token/{token}", response_model=EmptyResponse)
@log_exceptions
async def unregister_device_token(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: NotificationsAPI = Depends(_api),
):
    """Called on logout so the device stops getting pushes addressed to
    this account."""
    await api.unregister_device_token(client_id=client_id, token=token)
    await db.commit()
    return EmptyResponse()
