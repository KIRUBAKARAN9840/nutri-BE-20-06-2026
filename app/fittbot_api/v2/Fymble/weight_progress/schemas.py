"""Pydantic request/response models for Weight Progress."""

from datetime import date
from typing import List, Optional

from pydantic import BaseModel


# ── Response sub-models ───────────────────────────────────────────


class WeightProgressData(BaseModel):
    actual_weight: Optional[float] = None
    target_weight: Optional[float] = 0
    start_weight: Optional[float] = 0
    progress: float = 0


class RegistrationSteps(BaseModel):
    dob: bool = False
    goal: bool = False
    height: bool = False
    weight: bool = False
    body_shape: bool = False
    lifestyle: bool = False
    registration_complete: bool = False


class JourneyItem(BaseModel):
    id: int
    client_id: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_weight: float
    actual_weight: float
    target_weight: float
    days_diff: int

    class Config:
        from_attributes = True


class WeightRecord(BaseModel):
    id: int
    client_id: int
    weight: float
    status: bool
    date: date

    class Config:
        from_attributes = True


class WeightChartPoint(BaseModel):
    label: Optional[date] = None
    value: float = 0


# ── Combined response ──────────────��─────────────────────────────

class WeightProgressFullData(BaseModel):
    weight_progress: WeightProgressData
    registration_steps: RegistrationSteps
    usertype: str = "guest"
    bmi: Optional[float] = None
    bmi_status: Optional[str] = None
    gender: Optional[str] = None
    url: Optional[str] = None
    weight: List[WeightChartPoint] = []
    journey_list: List[JourneyItem] = []
    record_list: List[WeightRecord] = []


class WeightProgressResponse(BaseModel):
    status: int = 200
    data: WeightProgressFullData


# ── Add weight request/response ──────────────────────────────────

class AddWeightRequest(BaseModel):
    actual_weight: Optional[float] = None
    target_weight: Optional[float] = None
    start_weight: Optional[float] = None


class AddWeightResponse(BaseModel):
    status: int = 200
    message: str = "weight added successfully"
    journey_completion: bool = False
