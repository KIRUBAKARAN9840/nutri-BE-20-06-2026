"""Pydantic request/response models for Gym Membership Bookings (checkout preview)."""

from typing import Optional
from pydantic import BaseModel

from ..shared.schemas import GymAddress


# -- Request Schemas -----------------------------------------------------------


class ApplyCouponRequest(BaseModel):
    gym_id: int
    plan_id: int
    coupon_code: str


# -- Response Schemas ----------------------------------------------------------


class MembershipBookingResponse(BaseModel):
    """Response for gym membership booking price calculation."""

    status: int = 200
    gym_name: Optional[str] = None
    gym_logo: Optional[str] = None
    address: Optional[GymAddress] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    plan_name: Optional[str] = None
    plan_id: int
    duration: int = 0
    amount: Optional[int] = None
    original_amount_before_offer: Optional[int] = None
    offer_active: bool = False
    personal_training: bool = False
    plan_for: Optional[str] = None
    sessions_count: Optional[int] = None
    no_cost_emi: bool = False
    reward_amount: int = 0
    opted_in: bool = False
    daily_offer_active: bool = False
    walkaway_discount_active: bool = False
    walkaway_discount_amount: int = 0
    coupon_applied: bool = False
    coupon_discount_percent: int = 0
    coupon_discount_amount: Optional[int] = None
    amount_before_coupon: Optional[int] = None
    coupon_message: Optional[str] = None
