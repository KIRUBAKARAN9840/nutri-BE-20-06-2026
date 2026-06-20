"""Shared Pydantic schemas used across all booking domains."""

from typing import Optional
from pydantic import BaseModel


class GymAddress(BaseModel):
    door_no: Optional[str] = None
    building: Optional[str] = None
    street: Optional[str] = None
    area: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None


class GymInfo(BaseModel):
    """Standardised gym info returned by GymInfoRepository."""

    name: str
    location: Optional[str] = None
    city: Optional[str] = None
    address: Optional[GymAddress] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    owner_mobile: Optional[str] = None
