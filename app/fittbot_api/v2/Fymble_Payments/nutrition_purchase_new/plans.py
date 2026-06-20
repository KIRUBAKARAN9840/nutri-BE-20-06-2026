

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.fittbot_api.v1.payments.models.enums import StrEnum


class PlanKind(StrEnum):
    """What kind of grant a plan produces on successful payment."""
    session_package = "session_package"  # writes NutritionEligibility (sessions)
    ai_diet_coach = "ai_diet_coach"      # AI diet coach access only, no sessions
   


@dataclass(frozen=True)
class NutritionPlan:
    sku: str
    kind: PlanKind
    price_minor: int
    total_sessions: int
    session_schedule: List[Dict[str, Any]]
    bonus_credits: int
    validity_days: int
    plan_name: str
    reward_entries_count: int
    flow: str


class UnknownPlanError(ValueError):
    """Raised when a request references a SKU that is not in the catalog."""


_PLANS: Dict[str, NutritionPlan] = {
    "nutri_basic": NutritionPlan(
        sku="nutri_basic",
        kind=PlanKind.session_package,
        price_minor=19900,
        total_sessions=1,
        session_schedule=[
            {"seq": 1, "duration_minutes": 60, "unlock_after_days": 0},
        ],
        bonus_credits=0,
        validity_days=1,
        plan_name="Basic Nutrition Pack",
        reward_entries_count=1,
        flow="basic_nutrition_plan",
    ),
    "nutri_1m": NutritionPlan(
        sku="nutri_1m",
        kind=PlanKind.session_package,
        price_minor=249900,
        total_sessions=4,
        session_schedule=[
            {"seq": 1, "duration_minutes": 60, "unlock_after_days": 0},
            {"seq": 2, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 3, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 4, "duration_minutes": 60, "unlock_after_days": 7},
        ],
        bonus_credits=200,
        validity_days=180,
        plan_name="Nutrition Package - 4 Sessions",
        reward_entries_count=3,
        flow="expert_nutrition_plan",
    ),
    "nutrition_service_30": NutritionPlan(
        sku="nutrition_service_30",
        kind=PlanKind.session_package,
        price_minor=199900,
        total_sessions=4,
        session_schedule=[
            {"seq": 1, "duration_minutes": 60, "unlock_after_days": 0},
            {"seq": 2, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 3, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 4, "duration_minutes": 60, "unlock_after_days": 7},
        ],
        bonus_credits=200,
        validity_days=30,
        plan_name="Nutrition Package - 4 Sessions (30 days)",
        reward_entries_count=3,
        flow="expert_nutrition_plan",
    ),
    "nutri_3m": NutritionPlan(
        sku="nutri_3m",
        kind=PlanKind.session_package,
        price_minor=599900,
        total_sessions=12,
        session_schedule=(
            [{"seq": 1, "duration_minutes": 60, "unlock_after_days": 0}]
            + [
                {"seq": s, "duration_minutes": 30, "unlock_after_days": 7}
                for s in range(2, 12)
            ]
            + [{"seq": 12, "duration_minutes": 60, "unlock_after_days": 7}]
        ),
        bonus_credits=500,
        validity_days=90,
        plan_name="Elite Nutrition Pack",
        reward_entries_count=3,
        flow="elite_nutrition_plan",
    ),
    "nutri_1m_off": NutritionPlan(
        sku="nutri_1m_off",
        kind=PlanKind.session_package,
        price_minor=199900,
        total_sessions=4,
        session_schedule=[
            {"seq": 1, "duration_minutes": 60, "unlock_after_days": 0},
            {"seq": 2, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 3, "duration_minutes": 30, "unlock_after_days": 7},
            {"seq": 4, "duration_minutes": 60, "unlock_after_days": 7},
        ],
        bonus_credits=200,
        validity_days=180,
        plan_name="Nutrition Package - 4 Sessions (Offer)",
        reward_entries_count=3,
        flow="expert_nutrition_plan",
    ),
    "nutri_3m_off": NutritionPlan(
        sku="nutri_3m_off",
        kind=PlanKind.session_package,
        price_minor=499900,
        total_sessions=12,
        session_schedule=(
            [{"seq": 1, "duration_minutes": 60, "unlock_after_days": 0}]
            + [
                {"seq": s, "duration_minutes": 30, "unlock_after_days": 7}
                for s in range(2, 12)
            ]
            + [{"seq": 12, "duration_minutes": 60, "unlock_after_days": 7}]
        ),
        bonus_credits=500,
        validity_days=90,
        plan_name="Elite Nutrition Pack (Offer)",
        reward_entries_count=3,
        flow="elite_nutrition_plan",
    ),
    "ai_diet_coach": NutritionPlan(
        sku="ai_diet_coach",
        kind=PlanKind.ai_diet_coach,
        price_minor=49900,
        total_sessions=0,
        session_schedule=[],
        bonus_credits=100,
        validity_days=36500,  
        plan_name="AI Diet Coach Access",
        reward_entries_count=1,
        flow="ai_diet_coach",
    )
}

def get_plan(sku: str) -> NutritionPlan:
    plan = _PLANS.get(sku)
    if plan is None:
        raise UnknownPlanError(f"unknown_nutrition_plan_sku: {sku}")
    return plan


def get_plan_or_none(sku: str) -> Optional[NutritionPlan]:
    return _PLANS.get(sku)


def all_skus() -> List[str]:
    return list(_PLANS.keys())
