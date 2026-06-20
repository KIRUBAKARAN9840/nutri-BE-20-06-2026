"""Pydantic request/response models for Gym Membership listing."""

from typing import Any, Optional, List
from pydantic import BaseModel, Field

from ..shared.schemas import PaginationMeta


# -- Request Schemas ----------------------------------------------------------


class MembershipListParams(BaseModel):
    """Query parameters for listing membership gyms."""

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
    no_cost_emi: Optional[bool] = None
    membership_types: Optional[List[str]] = None


# -- Listing Response Schemas -------------------------------------------------


class MembershipGymResponse(BaseModel):
    """Single gym in the membership listing."""

    gym_id: int
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    area: Optional[str] = None
    distance_km: Optional[float] = None
    views: int = 0
    frequently_booked: bool = False
    membership_price: Optional[int] = None
    original_membership_price: Optional[int] = None
    plan_id: Optional[int] = None
    duration: Optional[int] = None
    no_cost_emi: bool = False
    offer_active: bool = False

    class Config:
        from_attributes = True


class MembershipListResponse(BaseModel):
    """Response for membership gym listing."""

    status: int = 200
    data: List[MembershipGymResponse]
    pagination: PaginationMeta
    walkaway_discount_active: bool = False


# -- Gym Details Response Schemas ---------------------------------------------


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


class PlanItem(BaseModel):
    plan_id: int
    plan_name: Optional[str] = None
    amount: int
    original_amount_before_offer: Optional[int] = None
    duration: int
    description: Optional[str] = None
    services: Optional[Any] = None
    personal_training: bool = False
    original: Optional[int] = None
    offer_active: bool = False
    bonus: Optional[int] = None
    bonus_type: Optional[str] = None
    pause: Optional[int] = None
    pause_type: Optional[str] = None
    fittbot_plan_offer: Optional[dict] = None
    is_couple: bool = False
    plan_for: Optional[str] = None
    buddy_count: Optional[int] = None
    nutritional_plan: Optional[dict] = None
    no_cost_emi: bool = False
    per_month: int
    user_saving_price: int = 0
    duplicate: bool = False
    sessions_count: Optional[int] = None
    discount: Optional[int] = None


class GymDetailsData(BaseModel):
    gym_id: int
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    address: GymAddress
    contact_number: Optional[str] = None
    services: Optional[Any] = None
    operating_hours: Optional[Any] = None
    gym_timings: Optional[Any] = None
    photos: List[GymPhotoItem] = []
    plans: List[PlanItem] = []
    no_cost_emi: bool = False
    exact_location: Optional[dict] = None
    daily_offer_active: bool = False
    walkaway_discount_active: bool = False
    walkaway_show_modal: bool = False


class GymDetailsResponse(BaseModel):
    status: int = 200
    data: GymDetailsData
