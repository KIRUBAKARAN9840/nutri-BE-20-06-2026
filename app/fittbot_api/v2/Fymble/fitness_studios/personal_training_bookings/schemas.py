"""Pydantic request/response models for Personal Training Bookings (checkout preview)."""

from typing import List, Optional
from pydantic import BaseModel


# ── Response Schemas ─────────────────────────────────────────────────


class PTBillingLine(BaseModel):

    type: str
    days: int
    price_per_day: int
    amount: int


class PTSlotDetail(BaseModel):
    """Selected slot info echoed back for frontend confirmation."""

    schedule_id: int
    start_time: str  # e.g. "7:00 AM"
    end_time: str  # e.g. "8:00 AM"


class PTTrainerDetail(BaseModel):
    """Trainer details for the booking preview."""

    trainer_id: int
    name: str
    profile_image: Optional[str] = None
    experience: Optional[float] = None


class GymAddress(BaseModel):
    door_no: Optional[str] = None
    building: Optional[str] = None
    street: Optional[str] = None
    area: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None


class PTBookingResponse(BaseModel):
    """Response for personal training booking price calculation."""

    status: int = 200
    gym_name: Optional[str] = None
    address: Optional[GymAddress] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    session_name: str = "Personal Training"
    trainer: Optional[PTTrainerDetail] = None
    slot: Optional[PTSlotDetail] = None
    session_price: Optional[int] = None
    actual_price: Optional[int] = None
    number_of_days: int
    dates: List[str] = []
    total_amount: Optional[int] = None
    billing_lines: List[PTBillingLine] = []
    show_modal: bool = False
    reward_amount: int = 0
    opted_in: bool = False
    session_offer_active: bool = False
    session_offer_eligible: bool = False
    session_count: int = 0
