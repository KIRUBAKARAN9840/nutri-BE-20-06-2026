"""Pydantic request/response models for the Profile module (v2)."""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── GET /profile/data ────────────────────────────────────────────────


class ProfileData(BaseModel):
    """Everything the Profile screen needs (no email)."""

    name: Optional[str] = None
    profile: Optional[str] = None
    contact: Optional[str] = None
    gender: Optional[str] = None
    dob: Optional[date] = None
    age: Optional[int] = None
    height: Optional[float] = None
    lifestyle: Optional[str] = None
    goal: Optional[str] = None


class ProfileResponse(BaseModel):
    status: int = 200
    data: ProfileData


# ── PUT /profile/details ─────────────────────────────────────────────


class UpdateDetailsPayload(BaseModel):
    """Update non-contact personal + fitness fields.

    Contact (mobile) and email are NOT editable here:
      - contact has its own OTP-protected flow
      - email is intentionally read-only on the profile screen
    """

    name: Optional[str] = Field(None, max_length=100)
    gender: Optional[str] = Field(None, max_length=20)
    dob: Optional[date] = None
    height: Optional[float] = Field(None, gt=0, lt=300)
    lifestyle: Optional[str] = Field(None, max_length=50)
    goal: Optional[str] = Field(None, max_length=50)


class UpdateDetailsResponse(BaseModel):
    status: int = 200
    message: str = "Profile updated successfully"
    targets_recalculated: bool = False
    data: ProfileData


# ── POST /profile/contact/initiate ───────────────────────────────────


class InitiateContactChangePayload(BaseModel):
    new_contact: str = Field(..., min_length=10, max_length=15)

    @field_validator("new_contact")
    @classmethod
    def _digits_only(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError("contact must contain digits only")
        return v


class InitiateContactChangeResponse(BaseModel):
    status: int = 200
    message: str = "OTP sent to both numbers"
    expires_in: int = 300
    sent_to_old: bool
    sent_to_new: bool


# ── POST /profile/contact/verify ─────────────────────────────────────


class VerifyContactChangePayload(BaseModel):
    old_otp: str = Field(..., min_length=4, max_length=8)
    new_otp: str = Field(..., min_length=4, max_length=8)


class VerifyContactChangeResponse(BaseModel):
    status: int = 200
    message: str = "Mobile number updated successfully"
    contact: str
