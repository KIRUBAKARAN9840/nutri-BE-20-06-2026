from typing import Any, Dict, List
from pydantic import BaseModel


class NutritionPurchasePreviewResponse(BaseModel):
    status: int = 200
    price: int = 299
    price_minor: int = 29900


class NutritionStatusResponse(BaseModel):
    status: int = 200
    credits: int = 0
    nutrition_purchased: bool = False
    is_unlimited: bool = False


class SlotInfo(BaseModel):
    schedule_id: int
    start_time: str
    end_time: str
    is_booked: bool


class DatesResponse(BaseModel):
    status: int = 200
    data: List[str]


class SlotsResponse(BaseModel):
    status: int = 200
    data: List[Dict[str, Any]]
