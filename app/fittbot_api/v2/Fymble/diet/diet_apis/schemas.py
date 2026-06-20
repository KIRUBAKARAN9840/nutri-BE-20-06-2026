"""Pydantic request/response models for Diet macros/micros."""

from typing import List, Optional
from pydantic import BaseModel

from app.fittbot_api.v2.Fymble.home.schemas import HomeSessionSlot


class GetMacrosMicrosResponse(BaseModel):
    status: int = 200
    credits: int = 0
    nutrition_purchased: bool = False
    diet_plan_assigned: bool = False
    data: dict


class SetTargetRequest(BaseModel):
    calories: Optional[int] = None
    protein: Optional[int] = None
    carbs: Optional[int] = None
    fat: Optional[int] = None
    fiber: Optional[int] = None
    sugar: Optional[int] = None


class CheckEligibilityResponse(BaseModel):
    status: int = 200
    eligibility: bool = False
    credits: int = 0
    is_unlimited: bool = False
    nutrition_purchased: bool = False
    nearby_sessions: List[HomeSessionSlot] = []


class MessageResponse(BaseModel):
    status: int = 200
    message: str


class DietCoachFoodItem(BaseModel):
    id: int
    label: str
    image_url: Optional[str] = None


class DietCoachFoodsResponse(BaseModel):
    status: int = 200
    preference: str
    data: List[DietCoachFoodItem]
