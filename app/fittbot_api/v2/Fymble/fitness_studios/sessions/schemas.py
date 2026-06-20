"""Pydantic request/response models for Session gym listing."""

from typing import Optional, List
from pydantic import BaseModel, Field

from ..shared.schemas import PaginationMeta


# ── Request Schemas ──────────────────────────────────────────────────


class SessionListParams(BaseModel):
    """Query parameters for listing session gyms."""

    session_id: int
    dates: List[str]  # List of YYYY-MM-DD
    search: Optional[str] = None
    city: Optional[str] = None
    area: Optional[str] = None
    pincode: Optional[str] = None
    state: Optional[str] = None
    client_lat: Optional[float] = None
    client_lng: Optional[float] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=10, ge=1, le=50)
    client_id: Optional[int] = None
    sort_price: bool = False
    sort_type: str = "ascending"
    session_low: bool = False


# ── Response Schemas ─────────────────────────────────────────────────


class SessionSlotItem(BaseModel):
    """A single available time slot for a session at a gym."""

    schedule_id: int
    start_time: str  # e.g. "7:00 AM"
    end_time: str  # e.g. "8:00 AM"
    available_slots: int


class SessionGymResponse(BaseModel):
    """Single gym in the session listing."""

    gym_id: int
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    area: Optional[str] = None
    distance_km: Optional[float] = None
    views: int = 0
    frequently_booked: bool = False
    session_price: Optional[int] = None
    session_offer_active: bool = False
    slots: List[SessionSlotItem] = []

    class Config:
        from_attributes = True


class SessionListResponse(BaseModel):
    """Response for session gym listing."""

    status: int = 200
    data: List[SessionGymResponse]
    session_name: Optional[str] = None
    session_offer_eligible: bool = False
    session_count: int = 0
    client_name: Optional[str] = None
    pagination: PaginationMeta


