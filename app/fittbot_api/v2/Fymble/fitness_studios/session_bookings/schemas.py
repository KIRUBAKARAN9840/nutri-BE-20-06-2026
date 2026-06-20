"""Pydantic request/response models for Session Bookings (checkout preview)."""

from typing import List, Optional
from pydantic import BaseModel

from ..shared.schemas import GymAddress


# ── Request Schemas ──────────────────────────────────────────────────


class SessionBookingParams(BaseModel):
    """Query parameters for session booking price calculation."""

    gym_id: int
    session_id: int
    schedule_id: int
    dates: List[str]  # List of YYYY-MM-DD


# ── Response Schemas ─────────────────────────────────────────────────


class SessionBillingLine(BaseModel):

    type: str
    days: int
    price_per_day: int
    amount: int


class SessionSlotDetail(BaseModel):
    """Selected slot info echoed back for frontend confirmation."""

    schedule_id: int
    start_time: str  # e.g. "7:00 AM"
    end_time: str  # e.g. "8:00 AM"


class SessionBookingResponse(BaseModel):
    """Response for session booking price calculation."""

    status: int = 200
    gym_name: Optional[str] = None
    address: Optional[GymAddress] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    session_name: Optional[str] = None
    slot: Optional[SessionSlotDetail] = None
    session_price: Optional[int] = None
    actual_price: Optional[int] = None
    number_of_days: int
    dates: List[str] = []
    total_amount: Optional[int] = None
    billing_lines: List[SessionBillingLine] = []
    show_modal: bool = False
    reward_amount: int = 0
    opted_in: bool = False
    session_offer_active: bool = False
    session_offer_eligible: bool = False
    session_count: int = 0
