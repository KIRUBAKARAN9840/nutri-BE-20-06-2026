"""Pydantic request/response models for Food Scanner API."""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ─── Response Models ────────────────────────────────────────
class MicroNutrients(BaseModel):
    """Micro-nutrient values."""
    calcium_mg: float = 0.0
    magnesium_mg: float = 0.0
    sodium_mg: float = 0.0
    potassium_mg: float = 0.0
    iron_mg: float = 0.0
    iodine_mcg: float = 0.0


class PlateTotals(BaseModel):
    """Macro-nutrient totals."""
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    fibre_g: float = 0.0
    sugar_g: float = 0.0


class FoodItem(BaseModel):
    """Detected food item with nutrients."""
    label: str
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    fibre_g: float = 0.0
    sugar_g: float = 0.0
    calcium_mg: float = 0.0
    magnesium_mg: float = 0.0
    sodium_mg: float = 0.0
    potassium_mg: float = 0.0
    iron_mg: float = 0.0
    iodine_mcg: float = 0.0


class AnalyzeResponseData(BaseModel):
    """Food analysis response data."""
    primary_food: str = ""  # Main dish name for heading
    items: List[Dict[str, Any]] = []  # Individual items with nutrition (only if multiple items)
    totals: Dict[str, float] = {}
    micro_nutrients: Dict[str, float] = {}
    insights: List[str] = []
    message: Optional[str] = None


class AnalyzeResponse(BaseModel):
    """Food analysis response wrapper."""
    status: int = 200
    data: AnalyzeResponseData


class PlateResponse(BaseModel):
    """Per-image analysis response."""
    image_index: int
    items: List[str] = []
    totals: PlateTotals
    micro_nutrients: MicroNutrients
    insights: List[str] = []


class AnalyzeMultipleImagesResponse(BaseModel):
    """Response for multiple image analysis."""
    status: int = 200
    plates: List[PlateResponse] = []


# ─── Request Models ─────────────────────────────────────────
class FoodItemInput(BaseModel):
    """Food item for text-based analysis."""
    name: str
    quantity: float = 1.0
    unit: str = "serving"


class AnalyzeTextRequest(BaseModel):
    """Request for text-based food analysis."""
    food_items: List[FoodItemInput]
    client_id: Optional[int] = None
    model: Optional[str] = None  # For testing: gpt-4o-mini, gpt-4o, etc.


class ScannedFoodItem(BaseModel):
    """A scanned food item with id and nutrition."""
    id: int
    name: str
    calories: int = 0
    protein_g: int = 0
    carbs_g: int = 0
    fat_g: int = 0
    fibre_g: int = 0
    sugar_g: int = 0


class ModifyItemsRequest(BaseModel):
    """Request to modify scanned food items (add/edit/delete)."""
    action: str  # "add", "delete", or "edit"
    item_id: Optional[int] = None  # required for delete/edit, not needed for add
    items: List[ScannedFoodItem]  # current items list from frontend
    edited_food: Optional[FoodItemInput] = None  # required for add/edit
    model: Optional[str] = None


class ModifyItemsResponse(BaseModel):
    """Response after modifying food items."""
    status: int = 200
    data: AnalyzeResponseData


class SaveFoodRequest(BaseModel):
    """Request to save scanned food."""
    client_id: int
    items: List[str]
    totals: Dict[str, float]
    micro_nutrients: Dict[str, float]
    meal: Optional[str] = None  # Auto-detect by time if not provided


class SaveFoodResponse(BaseModel):
    """Response after saving food."""
    status: int = 200
    message: str = "Food saved successfully"
    meal: Optional[str] = None
    reward_point: int = 0


# ─── Async Job Models ───────────────────────────────────────
class AnalyzeAsyncRequest(BaseModel):
    """Request for async image analysis."""
    client_id: Optional[int] = None
    food_scan: Optional[bool] = None
    webhook_url: Optional[str] = None


class AnalyzeAsyncResponse(BaseModel):
    """Response for async job submission."""
    status: int = 200
    job_id: str = ""
    message: str = "Analysis job queued"


class JobStatusResponse(BaseModel):
    """Response for job status check."""
    status: int = 200
    job_id: str = ""
    state: str = ""  # pending, processing, completed, failed
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ─── AI Diet Create Models ───────────────────────────────────
class ScannerData(BaseModel):
    """Scanner data from food analysis."""
    primary_food: str = ""  # Main dish name
    items: List[Any] = []  # Can be List[Dict] (new format) or List[str] (old format)
    totals: Dict[str, float] = {}
    micro_nutrients: Dict[str, float] = {}
    insights: List[str] = []


class CreateAIDietRequest(BaseModel):
    """Request to create AI diet entry from scanner."""
    client_id: int
    date: str  # ISO date string
    scanner_data: ScannerData
    gym_id: Optional[int] = None
    type: str = "scanner"
    meal_category: str  # "BreakFast", "Lunch", "Snacks", "Dinner"


class CreateAIDietResponse(BaseModel):
    """Response after creating AI diet entry."""
    status: int = 200
    reward_point: Optional[int] = None
    feedback: bool = False
    target: bool = False


# ─── Health Check Models ────────────────────────────────────
class CircuitBreakerStatus(BaseModel):
    """Circuit breaker status."""
    is_open: bool = False
    failures: int = 0


class ConcurrencyStatus(BaseModel):
    """Concurrency control status."""
    openai_semaphore_available: int = 0
    gemini_semaphore_available: int = 0
    cpu_semaphore_available: int = 0
    cpu_executor_threads: int = 0


class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    timestamp: str = ""
    metrics: Dict[str, Any] = {}
    circuit_breakers: Dict[str, CircuitBreakerStatus] = {}
    concurrency: ConcurrencyStatus
