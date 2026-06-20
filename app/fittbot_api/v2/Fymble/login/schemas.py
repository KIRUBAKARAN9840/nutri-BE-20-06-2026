"""Pydantic request/response models for client login & registration."""

from typing import Optional
from pydantic import BaseModel


# -- Login (public) ----------------------------------------------------------

class LoginRequest(BaseModel):
    mobile_number: str


# -- OTP Verification (public) -----------------------------------------------

class OTPVerifyRequest(BaseModel):
    data: str  # mobile number (same key as V1)
    otp: str


class ResendOTPRequest(BaseModel):
    data: str  # mobile number


# -- Registration (public -- same as V1) --------------------------------------

class RegisterRequest(BaseModel):
    name: str
    mobile_number: str
    gender: str
    location: str
    referral_id: Optional[str] = None
    platform: Optional[str] = None
    is_from_ad: Optional[bool] = False  # True when signup came in via an ad funnel
