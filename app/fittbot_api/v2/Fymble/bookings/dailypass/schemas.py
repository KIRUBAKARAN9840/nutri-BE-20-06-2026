"""Pydantic request/response models for Daily Pass active bookings."""

from typing import List, Optional
from pydantic import BaseModel

from ..shared.schemas import GymAddress


# ── Response Schemas ─────────────────────────────────────────────────


class DailyPassBookingDetail(BaseModel):
    """A single active/upcoming daily pass."""

    pass_id: str
    gym_id: int
    amount: float
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    locality: Optional[str] = None
    city: Optional[str] = None
    days_total: int
    booking_type: str = "single"
    head_count: int = 1
    booked_dates: List[str] = []
    current_day_id: Optional[str] = None
    selected_time: Optional[str] = None
    remaining_days: int
    next_dates: List[str]
    can_upgrade: bool
    actual_days: Optional[List[str]] = None
    rescheduled_days: Optional[List[str]] = None
    selected_dates: Optional[List[str]] = None
    per_user_price: Optional[float] = None
    address: Optional[GymAddress] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    owner_mobile: Optional[str] = None


class DailyPassListResponse(BaseModel):
    """Response for GET /all active daily passes."""

    status: int = 200
    client_id: str
    passes: List[DailyPassBookingDetail]
    nutrition_card_variant: Optional[str] = None
