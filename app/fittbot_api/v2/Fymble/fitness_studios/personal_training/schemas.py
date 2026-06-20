"""Pydantic request/response models for Personal Training gym listing."""

from typing import List, Optional

from pydantic import BaseModel, Field

from ..shared.schemas import PaginationMeta


# ── Request Schemas ──────────────────────────────────────────────────


class PTListParams(BaseModel):
    """Query parameters for listing personal training gyms."""

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


class PTSlotItem(BaseModel):
    """A single available time slot for personal training at a gym."""

    schedule_id: int
    start_time: str  # e.g. "7:00 AM"
    end_time: str  # e.g. "8:00 AM"
    available_slots: int


class PTTrainerInfo(BaseModel):
    """Primary trainer displayed for a gym in the listing."""

    trainer_id: int
    name: str
    profile_image: Optional[str] = None
    experience: Optional[float] = None
    slots: List[PTSlotItem] = []


class PTGymResponse(BaseModel):
    """Single gym in the personal training listing."""

    gym_id: int
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    area: Optional[str] = None
    distance_km: Optional[float] = None
    views: int = 0
    frequently_booked: bool = False
    session_price: Optional[int] = None
    session_offer_active: bool = False
    trainer: Optional[PTTrainerInfo] = None
    extra_trainers_count: int = 0

    class Config:
        from_attributes = True


class PTListResponse(BaseModel):
    """Response for personal training gym listing."""

    status: int = 200
    data: List[PTGymResponse]
    session_name: str = "Personal Training"
    session_offer_eligible: bool = False
    session_count: int = 0
    client_name: Optional[str] = None
    pagination: PaginationMeta


# ── Trainer list & slots endpoints ──────────────────────────────────


class PTTrainerListItem(BaseModel):
    """A single trainer in the other-trainers list."""

    trainer_id: int
    name: str
    profile_image: Optional[str] = None
    experience: Optional[float] = None


class PTTrainerListResponse(BaseModel):
    """Response for GET /trainers — other trainers at a gym."""

    status: int = 200
    gym_id: int
    trainers: List[PTTrainerListItem] = []


class PTTrainerSlotsResponse(BaseModel):
    """Response for GET /trainer_slots — slots for a specific trainer."""

    status: int = 200
    gym_id: int
    trainer: PTTrainerInfo
    session_price: Optional[int] = None
    session_offer_active: bool = False
