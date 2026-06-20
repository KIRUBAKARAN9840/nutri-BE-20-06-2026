"""Pydantic request/response models for the new nutrition purchase flow (4-session package)."""

from typing import List, Optional
from pydantic import BaseModel, Field


# ── Constants ────────────────────────────────────────────────────────

# The 4-session package schedule (immutable business rule)
SESSION_SCHEDULE = [
    {"seq": 1, "duration_minutes": 60, "unlock_after_days": 0},
    {"seq": 2, "duration_minutes": 30, "unlock_after_days": 7},
    {"seq": 3, "duration_minutes": 30, "unlock_after_days": 7},
    {"seq": 4, "duration_minutes": 60, "unlock_after_days": 7},
]

NUTRITION_PRICE = 1999
NUTRITION_PRICE_MINOR = 199900  # ₹1999 in paise


# ── Response Schemas ─────────────────────────────────────────────────


class NutritionPackagePreviewResponse(BaseModel):
    status: int = 200
    price: int = NUTRITION_PRICE
    price_minor: int = NUTRITION_PRICE_MINOR
    total_sessions: int = 4
    session_schedule: list = SESSION_SCHEDULE


class NutritionPackageStatusResponse(BaseModel):
    status: int = 200
    has_active_package: bool = False
    credits: int = 0
    total_sessions: int = 0
    sessions_used: int = 0
    sessions_remaining: int = 0
    next_session_number: Optional[int] = None
    next_session_duration: Optional[int] = None
    next_session_unlocked: bool = False
    next_unlock_date: Optional[str] = None
    eligibility_id: Optional[int] = None


class SlotInfo(BaseModel):
    schedule_id: int
    start_time: str
    end_time: str
    is_booked: bool = False
    duration_minutes: int = 60


class DatesResponse(BaseModel):
    status: int = 200
    data: List[str] = []


class SlotsResponse(BaseModel):
    status: int = 200
    data: List[SlotInfo] = []


# ── Request Schemas ──────────────────────────────────────────────────


class BookSlotRequest(BaseModel):
    schedule_id: int = Field(..., description="NutritionSchedule ID for the chosen slot")
    booking_date: str = Field(..., description="ISO date string YYYY-MM-DD")
    start_time: str = Field(..., description="Slot start time HH:MM (24hr)")
    end_time: str = Field(..., description="Slot end time HH:MM (24hr)")


class BookSlotResponse(BaseModel):
    status: int = 200
    message: str = "Slot booked successfully"
    booking_id: int
    session_number: int
    duration_minutes: int
    sessions_remaining: int
