
from datetime import time

from pydantic import BaseModel, Field

from .schemas import WaterStatus


class SetWaterTargetRequest(BaseModel):
    target_water: float = Field(..., gt=0, description="Daily water target in litres")


class SetWaterReminderRequest(BaseModel):
    reminder_type: str = Field(default="notification")
    is_recurring: bool = Field(default=True)
    water_timing: float = Field(..., gt=0)
    intimation_start_time: time
    intimation_end_time: time


class GetWaterResponse(BaseModel):
    status: int = 200
    message: str = "Data fetched successfully"
    data: WaterStatus


class AddWaterResponse(BaseModel):
    status: int = 200
    message: str = "Water qty added successfully"


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
