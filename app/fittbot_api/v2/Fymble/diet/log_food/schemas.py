"""Pydantic request/response models for Log Food."""

from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class LogFoodRequest(BaseModel):
    date: date
    diet_data: List
    # When logging from a nutritionist's diet template, frontend sends both
    # of these so the backend can record which (day, title) was logged.
    client_template_id: Optional[int] = None
    day_number: Optional[int] = None


class LogFoodResponse(BaseModel):
    status: int = 200
    message: str
    reward_point: int = 0
    xp_earned: int = 0
    feedback: bool = False
    target: bool = False


# ─── Scanner → Diet Models ────────────────────────────────────

class ScannerData(BaseModel):
    primary_food: str = ""
    items: List[Any] = []
    totals: Dict[str, float] = {}
    micro_nutrients: Dict[str, float] = {}
    insights: List[str] = []


class LogScannedFoodRequest(BaseModel):
    date: str
    scanner_data: ScannerData
    meal_category: str  # "BreakFast", "Lunch", "Snacks", "Dinner"
    gym_id: Optional[int] = None


class LogScannedFoodResponse(BaseModel):
    status: int = 200
    reward_point: int = 0
    xp_earned: int = 0
    feedback: bool = False
    target: bool = False
