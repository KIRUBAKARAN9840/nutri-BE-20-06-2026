"""Pydantic request/response models for Daily Pass Bookings."""

from datetime import date as date_type
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Request Schemas ──────────────────────────────────────────────────


class CalculateRewardParams(BaseModel):
    """Query parameters for reward calculation."""

    gym_id: int
    number_of_days: int = Field(ge=1)


class PromoApplyRequest(BaseModel):
    """Body for POST /apply_coupon."""

    coupon_code: str = Field(..., min_length=1, max_length=50)


class PromoApplyResponse(BaseModel):
    """Response for POST /apply_coupon."""

    status: int = 200
    valid: bool
    message: str
    coupon_code: Optional[str] = None


class PromoRedeemRequest(BaseModel):
    """Body for POST /redeem_promo."""

    coupon_code: str = Field(..., min_length=1, max_length=50)
    gym_id: int
    selected_date: date_type


# ── Response Schemas ─────────────────────────────────────────────────


class BillingLine(BaseModel):
    """Single line item for billing breakdown.

    type: "user_offer" | "user_actual" | "friend"
    Frontend renders each line, e.g. "3 days × ₹49 (Intro Offer) = ₹147"
    """
    type: str
    days: int
    price_per_day: int
    amount: int
    count: Optional[int] = None  # only present for type="friend"


class CalculateRewardResponse(BaseModel):
    """Response for /calculate_reward."""

    status: int = 200
    gym_name: Optional[str] = None
    operating_hours: Optional[list] = None
    dailypass_price: Optional[int] = None
    actual_price: Optional[int] = None
    number_of_days: int
    number_of_users: int = 1
    head_count: Optional[int] = None
    user_amount: Optional[int] = None
    friends_amount: Optional[int] = None
    per_user_amount: Optional[int] = None
    total_amount: Optional[int] = None
    billing_lines: List[BillingLine] = []
    show_modal_self: bool = False
    show_modal_friend: bool = False
    reward_amount: int = 0
    opted_in: bool = False
    show_coupon_code: bool = False


class PromoRedeemResponse(BaseModel):
    """Response for POST /redeem_promo. Matches UnifiedVerificationResponse format."""

    success: bool = True
    payment_captured: bool = True
    order_id: Optional[str] = None
    payment_id: str
    daily_pass_activated: bool = True
    daily_pass_details: Optional[Dict[str, Any]] = None
    subscription_activated: bool = False
    subscription_details: Optional[Dict[str, Any]] = None
    total_amount: int
    currency: str = "INR"
    message: str = "Promo code redeemed successfully"
