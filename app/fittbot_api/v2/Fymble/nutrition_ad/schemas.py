
from typing import Optional

from pydantic import BaseModel


class NutritionAdVisitResponse(BaseModel):
    status: int = 200
    message: str = "Visit recorded"
    visit_id: Optional[int] = None
