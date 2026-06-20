"""Pydantic request/response models for webinar registration."""

from typing import Optional
from pydantic import BaseModel, Field


class WebinarRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    mobile_number: str = Field(..., min_length=6, max_length=20)
    gender: str = Field(..., max_length=20)
    location: str = Field(..., max_length=255)
    aim: str = Field(..., max_length=1000)


class WebinarRegisterResponse(BaseModel):
    status: int = 200
    message: str = "Registration saved"
    webinar_id: Optional[int] = None
    is_update: bool = False



