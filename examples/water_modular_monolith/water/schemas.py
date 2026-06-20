"""Public Pydantic schemas — part of the cross-module contract.

These are returned by `WaterAPI` methods. Changing them is a breaking
change for every consumer; treat with the same care as a REST schema.
"""

from typing import List, Optional

from pydantic import BaseModel


class WaterIntakeData(BaseModel):
    target_litres: float = 0.0
    actual_litres: float = 0.0


class DayStreak(BaseModel):
    day: str
    percentage: float


class WaterReminderData(BaseModel):
    is_enabled: bool = False
    water_timing: Optional[float] = None
    intimation_start_time: Optional[str] = None
    intimation_end_time: Optional[str] = None
    is_recurring: bool = False


class WaterStatus(BaseModel):
    intake: WaterIntakeData
    last_drink_time: Optional[str] = None
    streak: List[DayStreak] = []
    reminder: WaterReminderData = WaterReminderData()


class WaterReminderCreated(BaseModel):
    reminder_id: int
    scheduled_reminder_time: str
