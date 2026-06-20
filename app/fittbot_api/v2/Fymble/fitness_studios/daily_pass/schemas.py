"""Pydantic request/response models for Daily Pass gym listing."""

from typing import Optional, List
from pydantic import BaseModel, Field

from ..shared.schemas import PaginationMeta


# ── Request Schemas ──────────────────────────────────────────────────


class DailyPassListParams(BaseModel):
    """Query parameters for listing dailypass gyms."""

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
    fitness_types: Optional[List[str]] = None
    dailypass_low: bool = False


# ── Response Schemas ─────────────────────────────────────────────────


class DailyPassGymResponse(BaseModel):
    """Single gym in the dailypass listing."""

    gym_id: int
    gym_name: Optional[str]
    cover_pic: Optional[str]
    area: Optional[str]
    distance_km: Optional[float]
    views: int = 0
    frequently_booked: bool = False
    dailypass_price: Optional[int]

    class Config:
        from_attributes = True


class DailyPassListResponse(BaseModel):
    """Response for dailypass gym listing."""

    status: int = 200
    data: List[DailyPassGymResponse]
    dailypass_offer_eligible: bool = False
    dailypass_count: int = 0
    client_name: Optional[str] = None
    pagination: PaginationMeta


# ── Gym Details Schemas ─────────────────────────────────────────────


class GymAddress(BaseModel):
    door_no: Optional[str] = None
    building: Optional[str] = None
    street: Optional[str] = None
    area: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None


class GymPhotoItem(BaseModel):
    photo_id: int
    type: str
    image_url: str


class GymDetailsResponse(BaseModel):
    """Response for single gym details."""

    status: int = 200
    gym_id: int
    gym_name: Optional[str] = None
    address: GymAddress
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    operating_hours: Optional[list] = None
    services: Optional[list] = None
    gym_pics: List[GymPhotoItem] = []
