"""HTTP request / response envelopes — private to routes.py.

These are NOT the cross-module DTOs (those live in schemas.py). These wrap
the DTOs with the standard {status, message, data} envelope used by every
v2.Fymble endpoint, and define the validated request body shapes.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from app.fittbot_api.v2.Fymble.gym_mate.friends import OnboardingSuggestionDTO
from .schemas import (
    DefaultAvatarDTO,
    FullProfileDTO,
    OnboardingStatusDTO,
    PresignedPhotoUploadDTO,
)


class OnboardingStep1Request(BaseModel):
    primary_goal: str = Field(..., description="One of PRIMARY_GOAL_VALUES")
    activity_interests: List[str] = Field(..., min_length=1)
    preferred_timing: str = Field(..., description="One of PREFERRED_TIMING_VALUES")
    gym_personality: str = Field(..., description="One of GYM_PERSONALITY_VALUES")
    city: str = Field(..., description="Current City You are Living in")


class OnboardingStep2Request(BaseModel):
    """Both photos and bio are optional. Sending an empty body just marks
    onboarding as complete without touching profile or profile_photo."""
    photo_paths: Optional[List[str]] = Field(None, max_length=3)
    bio: Optional[str] = Field(None, max_length=300)


class EditProfileRequest(BaseModel):

    primary_goal: Optional[str] = Field(None, max_length=30)
    activity_interests: Optional[List[str]] = Field(None, min_length=1)
    preferred_timing: Optional[str] = Field(None, max_length=30)
    gym_personality: Optional[str] = Field(None, max_length=30)
    bio: Optional[str] = Field(None, max_length=300)
    city: Optional[str] = Field(None, max_length=100)
    photo_paths: Optional[List[str]] = Field(None, max_length=3)


class PresignPhotoSlotItem(BaseModel):
    display_order: int = Field(..., ge=0, le=2)
    content_type: str = Field(..., description="image/jpeg, image/png, or image/webp")


class PresignPhotosRequest(BaseModel):
    slots: List[PresignPhotoSlotItem] = Field(..., min_length=1, max_length=3)


# ---------------------------------------------------------------------------
# Response envelopes — match {status, message, data} pattern.
# ---------------------------------------------------------------------------
class OnboardingStep1Response(BaseModel):
    status: int = 200
    message: str = "Step 1 saved"
    data: OnboardingStatusDTO
    gender: Optional[str] = None
    default_avatars: List[DefaultAvatarDTO] = []


class OnboardingStep2Response(BaseModel):
    status: int = 200
    message: str = "Onboarding complete"
    data: OnboardingStatusDTO
    # Seed the freshly-onboarded user's home: 3 random higher-match
    # gym mates if any exist, otherwise 9 random fallback profiles.
    # Each entry carries client_id + name + avatar_url so the FE can
    # render a populated "find friends" screen right after Step 2.
    suggested_gym_mates: List[OnboardingSuggestionDTO] = []


class OnboardingStatusResponse(BaseModel):
    status: int = 200
    data: OnboardingStatusDTO


class GetProfileResponse(BaseModel):
    status: int = 200
    data: FullProfileDTO
    # Gender from clients table + matching curated avatars. Powers the
    # photo picker on the edit-profile screen — same shape as Step 1.
    gender: Optional[str] = None
    default_avatars: List[DefaultAvatarDTO] = []


class EditProfileResponse(BaseModel):
    status: int = 200
    message: str = "Profile updated"
    data: FullProfileDTO
    gender: Optional[str] = None
    default_avatars: List[DefaultAvatarDTO] = []


class PresignPhotosResponse(BaseModel):
    status: int = 200
    message: str = "Upload URLs issued"
    data: List[PresignedPhotoUploadDTO]
