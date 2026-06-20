"""Events emitted by the profile module.

Public event *types* — other modules import these to subscribe.
Internal `EventBus` Protocol — abstracted so we can swap in a no-op for
tests or a different bus implementation without changing this module.

Note: nothing here imports `chat`, `friends`, `sessions`, etc. Those
modules subscribe to these events at startup. This module never knows
who's listening.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# ---------------------------------------------------------------------------
# Public event types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProfileOnboarded:
    """Fired exactly once per client, when Step 2 of onboarding completes.

    Subscribers (when they exist):
      - friends module → bootstrap suggestion candidates
      - notifications module → "welcome to GymMate" push
    """
    client_id: int
    profile_id: int


# ---------------------------------------------------------------------------
# Event bus port
# ---------------------------------------------------------------------------
class EventBus(Protocol):
    """The contract for publishing events.

    The default in-process implementation lives at the app level (one bus
    instance per process). A future Redis-Streams implementation would
    satisfy the same protocol so callers don't change.
    """

    async def publish(self, event: object) -> None: ...


class NoopEventBus:
    """Drops every event. Useful as a default during early development and
    in tests where you don't care about side effects."""

    async def publish(self, event: object) -> None:
        return None
