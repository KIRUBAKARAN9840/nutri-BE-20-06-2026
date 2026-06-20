
from typing import Dict, Optional

from pydantic import BaseModel, Field

from app.fittbot_api.v2.Fymble.home.schemas import NutritionPackageCard


# ── personal_status — mirrors home's nutrition keys, just nested ─────

class PersonalStatus(BaseModel):

    nutrition_purchased: bool = False
    diet_plan_assigned: bool = False
    not_attended: bool = False
    nutrition_booking_id: Optional[int] = None
    nutrition_schedule: Optional[dict] = None
    nutrition_package: Optional[NutritionPackageCard] = None


class NutritionPageResponse(BaseModel):
    status: Optional[int]=200
    ai: bool = False
    personal: bool = False
    create_plan: bool = False
    personal_status: PersonalStatus
    video: Dict[str, str] = Field(default_factory=dict)
