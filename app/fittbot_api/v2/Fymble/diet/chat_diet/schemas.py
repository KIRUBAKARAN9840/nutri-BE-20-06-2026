from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ─── Request ──────────────────────────────────────────────────────────


class ChatDietRequest(BaseModel):
    height: float = Field(..., ge=50, le=250)
    weight: float = Field(..., ge=20, le=300)
    target_weight: float = Field(..., ge=20, le=300)
    goal: Literal["muscle gain", "fat loss"]
    preferences: List[str]
    dietary_preference: str = Field(..., min_length=1)
    allergies: Optional[List[str]] = None
    other: Optional[str] = None


# ─── Plan structure ───────────────────────────────────────────────────


class MealItem(BaseModel):
    name: str
    calories: int
    protein: int
    carbs: int
    fat: int
    fiber: int
    sugar: int
    sodium: int
    calcium: int
    iron: int
    magnesium: int
    potassium: int
    ingredients: str
    recipe: str


class DayPlan(BaseModel):
    day: int
    target_calories: int = 0
    breakfast: List[MealItem]
    lunch: List[MealItem]
    dinner: List[MealItem]
    snacks: List[MealItem]


# ─── Responses ────────────────────────────────────────────────────────


class ChatDietGenerateResponse(BaseModel):
    """Response from POST /chat-diet/generate.

    Two shapes:
      - Cache hit  → status=200, plan filled, completed=true
      - Enqueued   → status=202, job_id + status_url, plan=null
    """
    status: int = 200
    success: bool = True
    job_id: str
    status_url: Optional[str] = None
    eta_seconds: Optional[int] = None
    plan: Optional[List[DayPlan]] = None
    plan_id: Optional[int] = None
    completed: bool = False
    message: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Response from GET /chat-diet/status/{job_id}."""
    status: int = 200
    job_id: str
    state: Literal["queued", "processing", "complete", "failed"]
    plan_id: Optional[int] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class JobPlanResponse(BaseModel):
    """Response from GET /chat-diet/plan/{job_id}."""
    status: int = 200
    plan_id: int
    plan: List[DayPlan]
    model_used: Optional[str] = None
    created_at: str


# ─── Follow-up flow ───────────────────────────────────────────────────


class FollowupEligibilityResponse(BaseModel):
    """Response from GET /chat-diet/followup/eligibility."""
    status: int = 200
    current_step: Optional[int] = None
    next_step: int = 0
    next_step_label: Literal["initial", "follow_up_1", "follow_up_2", "follow_up_3"]
    eligible: bool = False
    last_plan_id: Optional[int] = None
    last_plan_created_at: Optional[str] = None
    days_until_eligible: int = 0
    series_complete: bool = False


class FollowupGenerateRequest(BaseModel):
    """Body for POST /chat-diet/followup/generate."""
    feedback: Optional[str] = Field(None, max_length=1000)


# ─── Swap a single meal in an existing plan ────────────────────────────


MealType = Literal["breakfast", "lunch", "dinner", "snacks"]


class SwapMealRequest(BaseModel):
    """Body for POST /chat-diet/plans/{plan_id}/swap."""
    day: int = Field(..., ge=1, le=7)
    meal_type: MealType
    item_index: int = Field(0, ge=0, le=10)
    reason: Optional[str] = Field(None, max_length=500)


class SwapMealResponse(BaseModel):
    """Response from a successful swap.

    Frontend uses (day, meal_type, item_index) to patch local state with `new_item`.
    """
    status: int = 200
    plan_id: int
    day: int
    meal_type: MealType
    item_index: int
    previous_item: MealItem
    new_item: MealItem


# ─── Combined "current state" view (one call powers the diet page) ─────


_StepLabel = Literal["initial", "follow_up_1", "follow_up_2", "follow_up_3"]


class LatestPlanData(BaseModel):
    """The user's most recent plan, embedded inline."""
    plan_id: int
    step: int
    step_label: _StepLabel
    consumed_calories: int = 0
    plan: List[DayPlan]
    model_used: Optional[str] = None
    created_at: str


class FollowupInfo(BaseModel):
    """Eligibility envelope (no `status` field — wrapped by parent response)."""
    current_step: Optional[int] = None
    next_step: int = 0
    next_step_label: _StepLabel
    eligible: bool = False
    last_plan_id: Optional[int] = None
    last_plan_created_at: Optional[str] = None
    days_until_eligible: int = 0
    series_complete: bool = False


class CurrentPlanResponse(BaseModel):
    """Response from GET /chat-diet/current.

    Single endpoint that powers the diet page: the latest plan (or null if none)
    plus the follow-up state machine the UI needs to render the next-step CTA.
    Today's consumed calories live on ``latest_plan`` (single value, applies to
    whichever day the user is viewing). Per-day targets live on each
    ``latest_plan.plan[].target_calories``.
    """
    status: int = 200
    has_plan: bool = False
    latest_plan: Optional[LatestPlanData] = None
    followup: FollowupInfo
