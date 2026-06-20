"""Protocol + factory for the notifications module.

Only the read endpoints (bell-icon feed, unread count, mark-read,
device-token CRUD) go through this API. Write paths happen
indirectly — domain events from other modules flow through
`_bus.gymmate_event_bus` and the handlers in `_handlers.py` call the
service from their own DB session.
"""

from datetime import datetime
from typing import Optional, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from . import schemas as dto
from ._events import EventBus, NoopEventBus


class NotificationsAPI(Protocol):

    async def list_feed(
        self,
        recipient_client_id: int,
        before_at: Optional[datetime] = None,
        limit: int = 30,
    ) -> dto.NotificationPageDTO: ...

    async def unread_count(self, recipient_client_id: int) -> int: ...

    async def home_summary(
        self, recipient_client_id: int,
    ) -> dto.HomeNotificationsSummaryDTO: ...

    async def mark_read(
        self, recipient_client_id: int, notif_id: int,
    ) -> None: ...

    async def mark_all_read(self, recipient_client_id: int) -> int: ...

    async def mark_bucket_read(
        self, recipient_client_id: int, bucket: str,
    ) -> int: ...

    async def delete(
        self, recipient_client_id: int, notif_id: int,
    ) -> None: ...

    async def register_device_token(
        self, client_id: int, platform: str, token: str,
    ) -> dto.DeviceTokenDTO: ...

    async def unregister_device_token(
        self, client_id: int, token: str,
    ) -> None: ...


def build_notifications_api(
    db: AsyncSession,
    *,
    event_bus: Optional[EventBus] = None,
) -> NotificationsAPI:
    from ._repository import DeviceTokenRepository, NotificationRepository
    from ._service import NotificationService

    return NotificationService(
        repository=NotificationRepository(db),
        tokens=DeviceTokenRepository(db),
        event_bus=event_bus or NoopEventBus(),
    )
