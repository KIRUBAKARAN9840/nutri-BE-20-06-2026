

from pydantic import BaseModel


class NutritionCreditStatusResponse(BaseModel):
    status: int = 200
    credits_balance: int = 0
    nutrition_purchased: bool = False
    ai_diet_coach: bool = False
    elite: bool = False
    expert: bool = False
