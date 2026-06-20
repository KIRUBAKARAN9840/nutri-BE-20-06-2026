"""Pydantic request/response models for Session upcoming bookings."""

from typing import List, Optional
from pydantic import BaseModel

from ..shared.schemas import GymAddress


# ── Response Schemas ─────────────────────────────────────────────────


class SessionBookingDayItem(BaseModel):
    """A single booked day within a session purchase."""

    booking_id: int
    date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    status: Optional[str] = None
    checkin_token: Optional[str] = None


class SessionPurchaseGroup(BaseModel):
    """A session purchase with its booked days."""

    purchase_id: int
    session_id: Optional[int] = None
    session_name: Optional[str] = None
    trainer_id: Optional[int] = None
    trainer_name: Optional[str] = None
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    address: Optional[GymAddress] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    owner_mobile: Optional[str] = None
    purchased_at: Optional[str] = None
    sessions: List[SessionBookingDayItem]


class SessionUpcomingResponse(BaseModel):
    """Response for GET /upcoming session bookings."""

    status: int = 200
    data: List[SessionPurchaseGroup]
