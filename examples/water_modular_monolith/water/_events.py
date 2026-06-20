"""Events emitted by the water module.

These dataclasses are part of the PUBLIC contract (re-exported from
`water/__init__.py`). The bus protocol below is private — callers
provide their own bus impl when they call `build_water_api`.

Why events?
  - The original `service.py` reached into `home`/`diet` Redis keys to
    invalidate caches, and into `CalorieEvent`/`ClientTarget` to award
    XP. Both are violations: water doesn't own those concerns.
  - With events, water emits a fact ("a glass was added") and other
    modules subscribe and decide what to do. Water can change without
    breaking subscribers; subscribers can change without breaking water.

How to subscribe (in another module):
    bus.subscribe(WaterIntakeAdded, lambda evt: rewards.award_xp(evt))
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


# ─── Events (public — exported from __init__.py) ───────────────────────

@dataclass(frozen=True)
class WaterIntakeAdded:
    client_id: int
    total_litres: float
    target_litres: float
    occurred_at: datetime


@dataclass(frozen=True)
class WaterTargetSet:
    client_id: int
    target_litres: float
    occurred_at: datetime


# ─── Bus protocol (private to the module's internals) ──────────────────

class EventBus(Protocol):
    async def publish(self, event: object) -> None: ...


class NoopEventBus:
    """Default bus used when none is wired — drops events on the floor.

    Fine for tests and for environments without a real bus configured.
    Production composition root must inject a real bus.
    """

    async def publish(self, event: object) -> None:
        return None
