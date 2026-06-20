"""Pydantic request/response models for registration steps."""

from typing import Optional
from pydantic import BaseModel


# -- Registration Steps (authenticated -- client_id from JWT) -----------------

class DOBStepRequest(BaseModel):
    dob: str


class GoalStepRequest(BaseModel):
    goal: str


class HeightStepRequest(BaseModel):
    height: float


class WeightStepRequest(BaseModel):
    weight: float
    target_weight: float


class BodyShapeStepRequest(BaseModel):
    current_body_shape_id: str
    target_body_shape_id: str


class LifestyleStepRequest(BaseModel):
    lifestyle: str


# -- Generic Responses -------------------------------------------------------

class StepResponse(BaseModel):
    status: int = 200
    message: str
    data: Optional[dict] = None


class StepsStatusResponse(BaseModel):
    status: int = 200
    message: str
    data: Optional[dict] = None
