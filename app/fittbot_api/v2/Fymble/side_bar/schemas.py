"""Pydantic request/response models for Sidebar."""

from typing import Optional
from pydantic import BaseModel


class SidebarDataResponse(BaseModel):
    status: int = 200
    client_name: Optional[str] = None
    phone_number: Optional[str] = None
    credits: int = 0
    is_unlimited: bool = False
