"""Pydantic request/response models for Water Tracker."""

from datetime import time
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Requests ──────────────────────────────────────────────────────

class SetWaterTargetRequest(BaseModel):
    target_water: float = Field(..., gt=0, description="Daily water target in litres")


class SetWaterReminderRequest(BaseModel):
    reminder_type: str = Field(default="notification", description="notification or alarm")
    is_recurring: bool = Field(default=True)
    water_timing: float = Field(..., gt=0, description="Hours between reminders e.g. 0.5, 1.0, 2.0")
    intimation_start_time: time = Field(..., description="Reminder window start e.g. 08:00")
    intimation_end_time: time = Field(..., description="Reminder window end e.g. 22:00")


# ── Response sub-models ───────────────────────────────────────────

class WaterIntakeData(BaseModel):
    target: float = 0
    actual: float = 0


class DayStreak(BaseModel):
    day: str
    percentage: float


class WaterReminderData(BaseModel):
    is_enabled: bool = False
    water_timing: Optional[float] = None
    intimation_start_time: Optional[str] = None
    intimation_end_time: Optional[str] = None
    is_recurring: bool = False


class WaterData(BaseModel):
    water_intake: WaterIntakeData
    last_drink_time: Optional[str] = None
    streak: List[DayStreak] = []
    reminder: WaterReminderData = WaterReminderData()


# ── Responses ─────────────────────────────────────────────────────

class GetWaterResponse(BaseModel):
    status: int = 200
    message: str = "Data fetched successfully"
    data: WaterData


class AddWaterResponse(BaseModel):
    status: int = 200
    message: str = "Water qty added successfully"
    xp_earned: int = 0


class SetWaterTargetResponse(BaseModel):
    status: int = 200
    message: str = "Water target set successfully"


class SetWaterReminderResponse(BaseModel):
    status: int = 200
    message: str = "Water reminder set successfully"
    reminder_id: int
    scheduled_reminder_time: str


class DeleteWaterReminderResponse(BaseModel):
    status: int = 200
    message: str = "Water reminder deleted successfully"
