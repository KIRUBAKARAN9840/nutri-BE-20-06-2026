"""GymMate notifications module — bell-icon feed + FCM push pipeline.

Public exports:
    router                            FastAPI router (mount at /api/v2)
    gymmate_event_bus                 singleton type-dispatching bus,
                                      injected into other gym_mate
                                      modules' factories in place of NoopEventBus
    register_notification_handlers    wire on-startup; idempotent

DTOs:
    NotificationDTO, NotificationPageDTO, NotificationActorDTO,
    UnreadCountDTO, DeviceTokenDTO
"""

from ._bus import gymmate_event_bus
from ._handlers import register_notification_handlers
from .api import NotificationsAPI, build_notifications_api
from .routes import router
from .schemas import (
    DeviceTokenDTO,
    HomeFriendRequestsDotDTO,
    HomeGymMateConnectionsDTO,
    HomeNotificationDotDTO,
    HomeNotificationsSummaryDTO,
    NotificationActorDTO,
    NotificationDTO,
    NotificationPageDTO,
    UnreadCountDTO,
)


__all__ = [
    "router",
    "gymmate_event_bus",
    "register_notification_handlers",
    "NotificationsAPI",
    "build_notifications_api",
    "NotificationDTO",
    "NotificationPageDTO",
    "NotificationActorDTO",
    "UnreadCountDTO",
    "DeviceTokenDTO",
    "HomeNotificationDotDTO",
    "HomeFriendRequestsDotDTO",
    "HomeGymMateConnectionsDTO",
    "HomeNotificationsSummaryDTO",
]
