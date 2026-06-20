"""Shared Pydantic models used across all Fymble listing endpoints."""

from typing import Optional

from pydantic import BaseModel


class PaginationMeta(BaseModel):
    current_page: int
    total_pages: int
    total_count: int
    has_next: bool
    has_prev: bool
    limit: int


class GymAddress(BaseModel):
    door_no: Optional[str] = None
    building: Optional[str] = None
    street: Optional[str] = None
    area: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
