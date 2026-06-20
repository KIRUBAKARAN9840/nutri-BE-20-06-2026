"""gym_mate.profile — public surface.

The ONLY names other modules may import from this module:
"""

from .api import ProfileAPI, build_profile_api
from ._domain import build_profile_details
from .schemas import (
    FullProfileDTO,
    OnboardingStatusDTO,
    PhotoDTO,
    PresignedPhotoUploadDTO,
    ProfileMatchAttributesDTO,
    ProfileSummaryDTO,
)
from ._events import ProfileOnboarded
from ._storage import PresignSlotRequest
from .routes import router

__all__ = [
    # API port
    "ProfileAPI",
    "build_profile_api",
    # Public DTOs
    "FullProfileDTO",
    "OnboardingStatusDTO",
    "PhotoDTO",
    "PresignedPhotoUploadDTO",
    "PresignSlotRequest",
    "ProfileMatchAttributesDTO",
    "ProfileSummaryDTO",
    # Helpers
    "build_profile_details",
    # Events
    "ProfileOnboarded",
    # HTTP
    "router",
]
