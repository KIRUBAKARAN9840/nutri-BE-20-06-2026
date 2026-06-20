"""Pydantic request/response models for Diet Personal Templates."""

from typing import Dict, List, Optional
from pydantic import BaseModel


# ── Request Models ────────────────────────────────────────────────

class AddDietTemplateRequest(BaseModel):
    template_name: str
    diet_data: List


class UpdateDietTemplateRequest(BaseModel):
    id: int
    diet_data: List


class EditDietTemplateNameRequest(BaseModel):
    id: int
    template_name: str


# ── Response Models ───────────────────────────────────────────────

class NutritionTotals(BaseModel):
    calories: int = 0
    protein: int = 0
    carbs: int = 0
    fats: int = 0
    fiber: int = 0
    sugar: int = 0
    calcium: int = 0
    magnesium: int = 0
    iron: int = 0
    sodium: int = 0
    potassium: int = 0


class TemplateItem(BaseModel):
    id: int
    name: str
    diet_data: Optional[List]=None
    nutrition_totals: NutritionTotals


class TemplateListResponse(BaseModel):
    status: int = 200
    message: str
    data: List[TemplateItem]


class SingleTemplateData(BaseModel):
    id: int
    name: str
    diet_data: List


class SingleTemplateResponse(BaseModel):
    status: int = 200
    message: str
    data: SingleTemplateData


class AddedTemplateData(BaseModel):
    id: int
    client_id: int
    template_name: str
    diet_data: List


class AddTemplateResponse(BaseModel):
    status: int = 200
    message: str
    data: AddedTemplateData


class MessageResponse(BaseModel):
    status: int = 200
    message: str


# ── Common Food Models ───────────────────────────────────────────

class FoodItem(BaseModel):
    id: int
    name: str
    calories: int
    protein: float
    carbs: float
    fat: float
    fiber: float
    sugar: float
    quantity: str
    pic: Optional[str] = None
    calcium: Optional[float] = None
    magnesium: Optional[float] = None
    potassium: Optional[float] = None
    iron: Optional[float] = None
    sodium: Optional[float] = None


class CommonFoodResponse(BaseModel):
    status: int = 200
    message: str
    data: List[FoodItem]


class SearchFoodResponse(BaseModel):
    status: int = 200
    data: List[FoodItem]
