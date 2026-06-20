"""Type-dispatching event bus singleton.

Each gym_mate module already calls `await bus.publish(SomeEvent(...))`
on its domain events. By default that bus is `NoopEventBus()` —
nothing happens. We replace it module-wide with `gymmate_event_bus`
(this file's singleton) so the notification handlers get called.

Handlers are registered at app startup via `register_notification_handlers()`
in `_handlers.py`. They run inside the originating request's event loop
but on their OWN short-lived DB session (so a notification INSERT
failure doesn't roll back the user's primary action).
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Type


logger = logging.getLogger("gymmate.notifications.bus")


Handler = Callable[[Any], Awaitable[None]]


class GymMateEventBus:
    """Pub/sub by Python class. Multiple handlers per event type
    allowed; failures in one don't block the others."""

    def __init__(self):
        self._handlers: Dict[Type, List[Handler]] = {}

    def register(self, event_type: Type, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Any) -> None:
        handlers = self._handlers.get(type(event), [])
        for h in handlers:
            try:
                await h(event)
            except Exception:
                # Notifications are best-effort. A handler exception
                # MUST NOT bubble up and fail the originating request
                # (e.g. friend request, chat send).
                logger.exception(
                    "notification handler failed for %s",
                    type(event).__name__,
                )


# Shared singleton — imported by every gym_mate module's route factory
# in place of NoopEventBus.
gymmate_event_bus = GymMateEventBus()
