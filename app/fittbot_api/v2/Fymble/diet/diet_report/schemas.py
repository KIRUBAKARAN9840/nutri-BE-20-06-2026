"""Pydantic request/response models for Diet Report."""

import datetime
from typing import Optional, List
from pydantic import BaseModel


class DietReportRequest(BaseModel):
    date: Optional[datetime.date] = None


class MacroStatus(BaseModel):
    actual_avg: int
    recommended: int
    status: str  # "high", "low", "on_track"


class ReportSummary(BaseModel):
    days_tracked: int
    avg_calories: int
    avg_protein: int
    avg_carbs: int
    avg_fat: int
    calories_status: MacroStatus
    protein_status: MacroStatus
    carbs_status: MacroStatus
    fat_status: MacroStatus


class CustomMeal(BaseModel):
    title: str
    foodList: list


class DayMeals(BaseModel):
    date: datetime.date
    breakfast: list
    lunch: list
    dinner: list
    snacks: list
    custom_meals: List[CustomMeal] = []
    total_calories: int


class DailyCalories(BaseModel):
    date: datetime.date
    calories: int


class TodaysMacros(BaseModel):
    overall: dict  # {calories: {target, actual}, protein: ...}
    remaining: dict  # {calories: int, protein: int, ...}
    micros: dict  # {calcium: {actual, percentage}, ...}


class DietReportData(BaseModel):
    summary: ReportSummary
    todays_macros: TodaysMacros
    today: DayMeals
    last_7_days: List[DailyCalories]


class DietReportResponse(BaseModel):
    status: int = 200
    data: DietReportData
