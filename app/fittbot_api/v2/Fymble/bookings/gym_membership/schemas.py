"""Pydantic request/response models for Gym Membership bookings."""

from typing import List, Optional
from pydantic import BaseModel


# ── Response Schemas ─────────────────────────────────────────────────


class NutritionalPlan(BaseModel):
    consultations: int
    amount: int


class MembershipCard(BaseModel):
    """A single active/upcoming/paused gym membership card."""

    membership_id: int
    gym_id: Optional[str] = None
    gym_name: Optional[str] = None
    amount: Optional[float] = None
    duration: Optional[int] = None
    purchased_at: Optional[str] = None
    type: str
    status: str
    entitlement_id: Optional[str] = None
    expires_at: Optional[str] = None
    bonus: Optional[int] = None
    bonus_type: Optional[str] = None
    pause_available: bool = False
    pause: Optional[int] = None
    pause_type: Optional[str] = None
    continue_available: bool = False
    nutritional_plan: Optional[NutritionalPlan] = None


class GymMembershipData(BaseModel):
    """Inner data payload — same keys as V1."""

    profile: Optional[str] = None
    name: Optional[str] = None
    client_id: int
    contact: Optional[str] = None
    gender: Optional[str] = None
    uuid: Optional[str] = None
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    type: str = "normal"
    membership_status: Optional[str] = None
    membership_cards: List[MembershipCard]


class GymMembershipListResponse(BaseModel):
    """Response for GET /gym_membership/all — same wrapper as V1."""

    status: int = 200
    message: str = "Data retrieved successfully"
    data: GymMembershipData
