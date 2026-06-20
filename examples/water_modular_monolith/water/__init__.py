"""water module — public surface.

The ONLY names other modules may import from `water`. Anything else
(notably `_service`, `_repository`, `_cache`, `_domain`, `_http_schemas`)
is private to this folder and enforced by the import-linter contract.
"""

from .api import WaterAPI, build_water_api
from .schemas import (
    DayStreak,
    WaterIntakeData,
    WaterReminderData,
    WaterReminderCreated,
    WaterStatus,
)
from ._events import WaterIntakeAdded, WaterTargetSet

__all__ = [
    "WaterAPI",
    "build_water_api",
    "WaterStatus",
    "WaterIntakeData",
    "WaterReminderData",
    "WaterReminderCreated",
    "DayStreak",
    "WaterIntakeAdded",
    "WaterTargetSet",
]
